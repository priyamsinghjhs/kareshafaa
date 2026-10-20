"""Standards-based result ingestion: HL7 v2 ORU^R01 and FHIR R4 Bundles.

* Idempotent on the message control ID (MSH-10) / Bundle.identifier: a replayed message returns the
  original outcome and never creates a second case.
* Patient matching is deterministic (identifier + birth year when supplied). Anything uncertain goes to a
  human match-review queue; the system never guesses or auto-merges.
* Units (OBX-6), result status (OBX-11) and HPV + reflex cytology (two OBX) are passed to the clinical rules.
* Unknown test codes, multi-programme messages and missing PID never fail silently: the message is kept in
  the human review queue (ACK AE) - only non-HL7 input or non-ORU message types are rejected outright.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Case, IngestMessage, Organization, Patient, utcnow
from ..programmes import CLINICIAN_CONFIRMED_TIERS
from ..security import SYSTEM, Actor
from ..statemachine import S
from . import workflow as wf
from .errors import DomainError, Invalid, NotFound

# LOINC -> (programme, test). Unknown codes are never discarded: the message goes to the human review queue.
LOINC = {
    "29771-3": ("colorectal_fit", "FIT"), "77379-6": ("cervical", "HPV"), "10524-7": ("cervical", "CYTOLOGY"),
    "24606-6": ("breast", "MAMMO"), "4548-4": ("diabetes_hba1c", "HBA1C"), "59261-8": ("diabetes_hba1c", "HBA1C"),
    "1558-6": ("diabetes_hba1c", "FPG"), "14771-0": ("diabetes_hba1c", "FPG"),
}
CODE_TO_PROGRAMME = {k: v[0] for k, v in LOINC.items()}
HL7_STATUS = {"F": "final", "C": "corrected", "P": "preliminary", "X": "cancelled", "W": "entered-in-error",
              "D": "entered-in-error", "R": "preliminary", "I": "preliminary", "A": "amended"}


@dataclass
class ParsedResult:
    message_id: str
    patient_identifier: str | None
    birth_year: int | None
    programme: str | None
    value: float | None
    value_text: str | None
    unit: str | None = None
    test: str | None = None
    status: str = "final"
    cytology: str | None = None
    problem: str | None = None  # set when no programme could be determined (goes to human review)


def _field(seg: list[str], i: int) -> str:
    return seg[i] if len(seg) > i else ""


def _num_or_none(v: str) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _combine(control_id: str, ident: str | None, birth_year: int | None,
             obs: list[tuple[str, str, str | None, str, str | None]]) -> ParsedResult:
    """obs: (loinc, raw value, unit, status, raw text). Combines HPV + reflex cytology; flags conflicts."""
    known = [(LOINC[c], v, u, st, c) for c, v, u, st, _ in obs if c in LOINC]
    if not known:
        codes = ",".join(sorted({c for c, *_ in obs})) or "none"
        return ParsedResult(control_id, ident, birth_year, None, None, None,
                            problem=f"no recognised screening test code (LOINC: {codes})")
    progs = {k[0][0] for k in known}
    if len(progs) > 1:
        return ParsedResult(control_id, ident, birth_year, None, None, None,
                            problem="results for more than one programme in one message")
    prog = progs.pop()
    statuses = [HL7_STATUS.get((st or "F").upper()[:1], st.lower() if st else "final") for *_, st, _ in known]
    status = next((x for x in statuses if x != "final"), "final")
    main = next((k for k in known if k[0][1] != "CYTOLOGY"), known[0])
    (_, test), raw, unit, _, code = main
    cyto = next((v for (pt, v, u, st, c) in known if pt[1] == "CYTOLOGY" and main is not None and c != code), None)
    if test == "CYTOLOGY":
        return ParsedResult(control_id, ident, birth_year, prog, None, raw.strip() or None, None, "CYTOLOGY", status)
    if prog in ("colorectal_fit", "diabetes_hba1c"):
        v = _num_or_none(raw)
        return ParsedResult(control_id, ident, birth_year, prog, v, None if v is not None else (raw.strip() or None),
                            unit or None, test, status)
    return ParsedResult(control_id, ident, birth_year, prog, None, raw.strip() or None, None, test, status,
                        cytology=(cyto or "").strip() or None)


def parse_hl7v2(raw: str) -> ParsedResult:
    text = raw.replace("\r\n", "\r").replace("\n", "\r").strip()
    segs = [s.split("|") for s in text.split("\r") if s.strip()]
    if not segs or segs[0][0] != "MSH":
        raise Invalid("not an HL7 v2 message (missing MSH)")
    msh = segs[0]
    # MSH-1 is the field separator itself, so MSH-n is at index n-1
    msg_type, control_id = _field(msh, 8), _field(msh, 9)
    if not msg_type.startswith("ORU^R01"):
        raise Invalid(f"unsupported message type {msg_type or '?'} (expected ORU^R01)")
    if not control_id:  # never drop: derive a content-based id so the message is still idempotent and reviewable
        import hashlib

        control_id = "NOID-" + hashlib.sha256(text.encode()).hexdigest()[:16]
    pid = next((s for s in segs if s[0] == "PID"), None)
    ident = birth_year = None
    if pid is not None:
        ident = _field(pid, 3).split("^")[0] or None
        dob = _field(pid, 7)
        birth_year = int(dob[:4]) if len(dob) >= 4 and dob[:4].isdigit() else None
    obs = []
    for obx in (s for s in segs if s[0] == "OBX"):
        code = _field(obx, 3).split("^")[0]
        raw_val = _field(obx, 5).split("^")
        val = raw_val[1] if len(raw_val) > 1 and raw_val[1] else raw_val[0]
        obs.append((code, val, _field(obx, 6).split("^")[0] or None, _field(obx, 11) or "F", val))
    r = _combine(control_id, ident, birth_year, obs)
    if pid is None and r.problem is None:
        r.problem = "PID segment missing"
    return r


def parse_fhir_bundle(bundle: dict[str, Any]) -> ParsedResult:
    if bundle.get("resourceType") != "Bundle":
        raise Invalid("expected a FHIR Bundle")
    mid = (bundle.get("identifier") or {}).get("value") or bundle.get("id")
    if not mid:
        raise Invalid("Bundle.identifier.value (or Bundle.id) is required for idempotency")
    res = [e.get("resource", {}) for e in bundle.get("entry", [])]
    pat = next((r for r in res if r.get("resourceType") == "Patient"), None)
    ident = birth_year = None
    if pat is not None:
        ident = next((i.get("value") for i in pat.get("identifier", []) if i.get("value")), None)
        bd = pat.get("birthDate") or ""
        birth_year = int(bd[:4]) if bd[:4].isdigit() else None
    fhir_status = {"final": "F", "amended": "A", "corrected": "C", "preliminary": "P", "registered": "P",
                   "cancelled": "X", "entered-in-error": "W"}
    obs = []
    for o in (r for r in res if r.get("resourceType") == "Observation"):
        codes = [c.get("code") for c in (o.get("code") or {}).get("coding", [])]
        code = next((c for c in codes if c in LOINC), codes[0] if codes else "")
        q = o.get("valueQuantity") or {}
        if q:
            val, unit = str(q.get("value", "")), q.get("unit") or q.get("code")
        else:
            val, unit = o.get("valueString") or (o.get("valueCodeableConcept") or {}).get("text") or "", None
        obs.append((code, val, unit, fhir_status.get(o.get("status", "final"), "F"), val))
    r = _combine(str(mid), ident, birth_year, obs)
    if pat is None and r.problem is None:
        r.problem = "Bundle has no Patient"
    return r


def _match(db: Session, r: ParsedResult) -> tuple[Patient | None, str | None]:
    if not r.patient_identifier:
        return None, "no patient identifier"
    p = db.scalar(select(Patient).where(Patient.mrn == r.patient_identifier))
    if p is None:
        return None, "identifier not found"
    if r.birth_year is not None and p.birth_year != r.birth_year:
        return None, "identifier found but birth year differs"
    return p, None


def _create(db: Session, p: Patient, r: ParsedResult, actor: Actor, now: datetime) -> tuple[int, Case | None]:
    lab = db.scalar(select(Organization).where(Organization.kind == "lab"))
    rep, case = wf.ingest_result(db, p, r.programme, value=r.value, value_text=r.value_text, unit=r.unit, test=r.test,
                                 status=r.status, cytology=r.cytology,
                                 performer_org_id=lab.id if lab else p.managing_org_id, actor=actor, now=now)
    if case is not None and case.tier not in CLINICIAN_CONFIRMED_TIERS and S(case.state) == S.ISSUED \
            and not case.review_required:
        wf.transition(db, case, S.TRIAGED, SYSTEM, now=now, note="auto-triaged by deterministic programme rule")
    return rep.id, case


def ingest(db: Session, source: str, r: ParsedResult, actor: Actor, now: datetime | None = None) -> IngestMessage:
    now = now or utcnow()
    prior = db.scalar(select(IngestMessage).where(IngestMessage.message_id == r.message_id))
    if prior is not None:
        audit.record(db, actor, "E", "ingest.duplicate", entity_type="IngestMessage", entity_id=prior.id, ts=now,
                     detail={"message_id": r.message_id})
        return prior
    msg = IngestMessage(source=source, message_id=r.message_id, received_at=now, patient_identifier=r.patient_identifier,
                        birth_year=r.birth_year, programme=r.programme, value=r.value,
                        value_text=(r.value_text or "")[:60] or None, unit=(r.unit or "")[:30] or None, test=r.test,
                        result_status=r.status, cytology=(r.cytology or "")[:30] or None, status="processed")
    p, why = _match(db, r)
    if r.programme is None:
        msg.status, msg.error = "needs_review", r.problem or "result could not be interpreted"
    elif p is None:
        msg.status, msg.error = "needs_match_review", why
    else:
        msg.report_id, case = _create(db, p, r, actor, now)
        msg.case_id = case.id if case else None
    db.add(msg)
    db.flush()
    audit.record(db, actor, "C", f"ingest.{source}", entity_type="IngestMessage", entity_id=msg.id,
                 case_id=msg.case_id, ts=now, detail={"status": msg.status, "programme": r.programme,
                                                      "reason": msg.error})
    return msg


def resolve_match(db: Session, msg_id: int, patient_id: int, actor: Actor, now: datetime | None = None) -> IngestMessage:
    """A human confirms which patient an unmatched result belongs to."""
    now = now or utcnow()
    if not actor.is_human or not actor.can("case:create"):
        from .errors import Forbidden

        raise Forbidden("only authorised staff can resolve a patient match")
    from ..signoff import require_signoff

    require_signoff(actor, "ingest.match.resolve")
    msg = db.get(IngestMessage, msg_id)
    if msg is None:
        raise NotFound("message not found")
    if msg.status != "needs_match_review":
        raise DomainError("message is not awaiting match review")
    p = db.get(Patient, patient_id)
    if p is None:
        raise NotFound("patient not found")
    if not msg.programme:
        raise DomainError("the test could not be identified: handle this message manually with the lab")
    r = ParsedResult(msg.message_id, msg.patient_identifier, msg.birth_year, msg.programme, msg.value, msg.value_text,
                     msg.unit, msg.test, msg.result_status or "final", msg.cytology)
    msg.report_id, case = _create(db, p, r, actor, now)
    msg.case_id = case.id if case else None
    msg.status = "resolved"
    msg.resolved_by_user_id = actor.user_id
    audit.record(db, actor, "U", "ingest.match_resolved", entity_type="IngestMessage", entity_id=msg.id,
                 case_id=msg.case_id, ts=now, detail={"patient_id": p.id})
    return msg


def ack_hl7(msg: IngestMessage | None, control_id: str, error: str | None = None) -> str:
    """Minimal HL7 v2 ACK (AA = accepted, AE = application error)."""
    ts = utcnow().strftime("%Y%m%d%H%M%S")
    code = "AE" if error or (msg and msg.status in ("needs_match_review", "needs_review")) else "AA"
    text = error or (msg.error if msg and msg.error else ("accepted" if msg else ""))
    return "\r".join([f"MSH|^~\\&|WASL|PROGRAMME|LAB|LAB|{ts}||ACK^R01|ACK{control_id}|P|2.5.1",
                      f"MSA|{code}|{control_id}|{text}"])
