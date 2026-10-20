"""Standards-based result ingestion: HL7 v2 ORU^R01 and FHIR R4 Bundles.

* Idempotent on the message control ID (MSH-10) / Bundle.identifier: a replayed message returns the
  original outcome and never creates a second case.
* Patient matching is deterministic (identifier + birth year when supplied). Anything uncertain goes to a
  human match-review queue; the system never guesses or auto-merges.
* The parser is intentionally minimal (prototype): one PID and the first recognised OBX/Observation.
"""
from __future__ import annotations

from ..programmes import CLINICIAN_CONFIRMED_TIERS

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Case, IngestMessage, Organization, Patient, utcnow
from ..programmes import PROGRAMMES
from ..security import SYSTEM, Actor
from ..statemachine import S
from . import workflow as wf
from .errors import DomainError, Invalid, NotFound

CODE_TO_PROGRAMME = {p.screening_code: k for k, p in PROGRAMMES.items()}
NUMERIC = {"colorectal_fit", "diabetes_hba1c"}


@dataclass
class ParsedResult:
    message_id: str
    patient_identifier: str | None
    birth_year: int | None
    programme: str
    value: float | None
    value_text: str | None


def _field(seg: list[str], i: int) -> str:
    return seg[i] if len(seg) > i else ""


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
    if not control_id:
        raise Invalid("MSH-10 message control ID is required")
    pid = next((s for s in segs if s[0] == "PID"), None)
    if pid is None:
        raise Invalid("PID segment is required")
    ident = _field(pid, 3).split("^")[0] or None
    dob = _field(pid, 7)
    birth_year = int(dob[:4]) if len(dob) >= 4 and dob[:4].isdigit() else None
    for obx in (s for s in segs if s[0] == "OBX"):
        code = _field(obx, 3).split("^")[0]
        prog = CODE_TO_PROGRAMME.get(code)
        if prog is None:
            continue
        raw_val = _field(obx, 5).split("^")
        val = raw_val[1] if len(raw_val) > 1 and raw_val[1] else raw_val[0]
        if prog in NUMERIC:
            try:
                return ParsedResult(control_id, ident, birth_year, prog, float(val), None)
            except ValueError as e:
                raise Invalid(f"OBX-5 must be numeric for {prog}") from e
        return ParsedResult(control_id, ident, birth_year, prog, None, val.strip() or None)
    raise Invalid("no OBX with a recognised screening code (LOINC) was found")


def parse_fhir_bundle(bundle: dict[str, Any]) -> ParsedResult:
    if bundle.get("resourceType") != "Bundle":
        raise Invalid("expected a FHIR Bundle")
    mid = (bundle.get("identifier") or {}).get("value") or bundle.get("id")
    if not mid:
        raise Invalid("Bundle.identifier.value (or Bundle.id) is required for idempotency")
    res = [e.get("resource", {}) for e in bundle.get("entry", [])]
    pat = next((r for r in res if r.get("resourceType") == "Patient"), None)
    if pat is None:
        raise Invalid("Bundle must contain a Patient")
    ident = next((i.get("value") for i in pat.get("identifier", []) if i.get("value")), None)
    bd = pat.get("birthDate") or ""
    birth_year = int(bd[:4]) if bd[:4].isdigit() else None
    for obs in (r for r in res if r.get("resourceType") == "Observation"):
        codes = [c.get("code") for c in (obs.get("code") or {}).get("coding", [])]
        prog = next((CODE_TO_PROGRAMME[c] for c in codes if c in CODE_TO_PROGRAMME), None)
        if prog is None:
            continue
        if prog in NUMERIC:
            q = (obs.get("valueQuantity") or {}).get("value")
            if not isinstance(q, (int, float)):
                raise Invalid(f"Observation.valueQuantity.value must be numeric for {prog}")
            return ParsedResult(str(mid), ident, birth_year, prog, float(q), None)
        txt = obs.get("valueString") or (obs.get("valueCodeableConcept") or {}).get("text")
        return ParsedResult(str(mid), ident, birth_year, prog, None, txt)
    raise Invalid("no Observation with a recognised screening code (LOINC) was found")


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
    rep, case = wf.ingest_result(db, p, r.programme, value=r.value, value_text=r.value_text,
                                 performer_org_id=lab.id if lab else p.managing_org_id, actor=actor, now=now)
    if case is not None and case.tier not in CLINICIAN_CONFIRMED_TIERS and S(case.state) == S.ISSUED:
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
                        birth_year=r.birth_year, programme=r.programme, value=r.value, value_text=r.value_text,
                        status="processed")
    p, why = _match(db, r)
    if p is None:
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
    msg = db.get(IngestMessage, msg_id)
    if msg is None:
        raise NotFound("message not found")
    if msg.status != "needs_match_review":
        raise DomainError("message is not awaiting match review")
    p = db.get(Patient, patient_id)
    if p is None:
        raise NotFound("patient not found")
    r = ParsedResult(msg.message_id, msg.patient_identifier, msg.birth_year, msg.programme or "", msg.value, msg.value_text)
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
    code = "AE" if error or (msg and msg.status == "needs_match_review") else "AA"
    text = error or (msg.error if msg and msg.error else ("accepted" if msg else ""))
    return "\r".join([f"MSH|^~\\&|WASL|PROGRAMME|LAB|LAB|{ts}||ACK^R01|ACK{control_id}|P|2.5.1",
                      f"MSA|{code}|{control_id}|{text}"])
