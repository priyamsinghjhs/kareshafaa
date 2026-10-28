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

import hashlib
import re
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..calendar_uae import as_utc_naive, to_utc
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
    flags: list[str] = field(default_factory=list)  # message-level review reasons (e.g. multiple_results)
    alt_identifiers: list[str] = field(default_factory=list)  # further patient identifiers (PID-3 repetitions)
    other_observations: list[list] = field(default_factory=list)  # [loinc, test, raw value, unit] not chosen
    payload_sha256: str | None = None
    collected_at: datetime | None = None  # specimen collection time (UTC, naive) if the message carries one
    # name / date of birth / sex / phone as sent by the lab: only used to PRE-FILL a registration when the result
    # matches no patient (never to match or overwrite a record - matching stays on identifiers)
    demographics: dict[str, Any] = field(default_factory=dict)


_HL7_SEX = {"F": "female", "M": "male", "O": "other", "U": "unknown", "A": "other", "N": "unknown"}


def _dob(v: str, hl7: bool) -> str | None:
    v = (v or "").strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", v) if hl7 else re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date().isoformat()
    except ValueError:
        return None


def _clip(v: Any, n: int = 100) -> str | None:
    v = str(v or "").strip()
    return v[:n] or None


# a result whose specimen is older than this at receipt is reviewed (repeat the test, or act on it?) - CONFIRM
STALE_RESULT_DAYS = 180
FUTURE_TOLERANCE = timedelta(hours=24)  # lab and server clocks / time zones may disagree by hours, not days
_HL7_TS = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})?(\d{2})?(?:\.\d+)?)?([+-]\d{4})?$")


def parse_collection_time(v: Any, hl7: bool) -> datetime | None:
    """HL7 TS (YYYYMMDD[HHMM[SS]][+ZZZZ]) or FHIR dateTime -> UTC naive. No offset = UAE local time. Bad -> None."""
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip()
    try:
        if hl7:
            m = _HL7_TS.match(v)
            if not m:
                return None
            y, mo, d, hh, mi, ss, off = m.groups()
            dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
            if off:
                sign = 1 if off[0] == "+" else -1
                return dt - sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
            return to_utc(dt)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v or len(v) == 10 else None
        if dt is None:
            return None
        return as_utc_naive(dt) if dt.tzinfo is not None else to_utc(dt)
    except (ValueError, OverflowError):
        return None


def _field(seg: list[str], i: int) -> str:
    return seg[i] if len(seg) > i else ""


_NUMERIC = re.compile(r"^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*$")


def _num_or_none(v: str) -> float | None:
    # strict decimal syntax: Python's float() also accepts "1_000" (-> 1000), "nan" and "inf"; anything that is
    # not a plain decimal number stays TEXT so the clinical rules send it to human review
    if not isinstance(v, str) or not _NUMERIC.match(v):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


_BIRADS_CAT = re.compile(r"([0-6])\s*([abc])?(?![0-9])", re.I)


def _severity(prog: str, test: str, raw: str, unit: str | None, cyto: str | None) -> tuple:
    """Severity of one observation by the clinical rules alone (lower = more severe)."""
    from ..clinical_rules import TIER_RANK, PatientContext, ResultInput, evaluate  # noqa: PLC0415

    v = _num_or_none(raw) if prog in ("colorectal_fit", "diabetes_hba1c") else None
    d = evaluate(ResultInput(programme=prog, value=v, value_text=None if v is not None else (raw.strip() or None),
                             unit=unit, test=test, cytology=cyto), PatientContext(age=50))  # in-range context
    # ties (e.g. prediabetes HbA1c vs diabetic-range FPG, both routine) -> the diagnostic-range result
    # last tie-breaker: magnitude within the same tier (BI-RADS 5 over 4, 4C over 4A; FIT 400 over 150) so the
    # primary value shown on the case is the most suspicious one (all values are kept either way)
    mag: tuple = ("", 0.0)
    if d.normalised:
        k, x = next(iter(d.normalised.items()))
        mag = (k, -float(x))
    elif prog == "breast" and (m := _BIRADS_CAT.search(raw or "")):
        mag = ("birads", -(int(m.group(1)) * 10 + " abc".index((m.group(2) or " ").lower())))
    return (0 if d.disposition != "negative" else 1, TIER_RANK.get(d.clinical_tier or d.tier or "", 9),
            0 if d.needs_confirmation else 1, mag)


def _combine(control_id: str, ident: str | None, birth_year: int | None,
             obs: list[tuple[str, str, str | None, str, str | None]]) -> ParsedResult:
    """obs: (loinc, raw value, unit, status, raw text). Combines HPV + reflex cytology; flags conflicts.

    Several results for the same programme in one message (left and right breast, two FIT samples, HbA1c and
    FPG together...) are never reduced to "the first one": the MOST SEVERE by the clinical rules is used and,
    if the values differ, the case goes to human review (reason multiple_results)."""
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
    mains = [k for k in known if k[0][1] != "CYTOLOGY"]
    cytos = [k for k in known if k[0][1] == "CYTOLOGY"]
    flags: list[str] = []
    if not mains:
        if len({(v or "").strip().lower() for (_, v, *_r) in cytos}) > 1:
            flags.append("multiple_results")
        (_, test), raw, unit, _, code = cytos[0]
        return ParsedResult(control_id, ident, birth_year, prog, None, raw.strip() or None, None, "CYTOLOGY", status,
                            flags=flags, other_observations=[[k[4], k[0][1], k[1], k[2]] for k in cytos[1:]] if flags else [])
    cyto_vals = sorted({(v or "").strip() for (_, v, *_r) in cytos if (v or "").strip()})
    if len(cyto_vals) > 1:
        flags.append("multiple_results")
    cyto = None
    if cyto_vals:  # the most severe cytology alongside an HPV result
        cyto = min(cyto_vals, key=lambda c: _severity(prog, "HPV", "positive", None, c)) if len(cyto_vals) > 1 \
            else cyto_vals[0]
    if len({((v or "").strip().lower(), (u or "").strip().lower(), t[1]) for (t, v, u, *_r) in mains}) > 1:
        flags.append("multiple_results")
    main = min(mains, key=lambda k: _severity(prog, k[0][1], k[1], k[2], cyto)) if len(mains) > 1 else mains[0]
    (_, test), raw, unit, _, code = main
    others = [[k[4], k[0][1], k[1], k[2]] for k in known if k is not main] if flags else []
    if prog in ("colorectal_fit", "diabetes_hba1c"):
        v = _num_or_none(raw)
        return ParsedResult(control_id, ident, birth_year, prog, v, None if v is not None else (raw.strip() or None),
                            unit or None, test, status, flags=sorted(set(flags)), other_observations=others)
    return ParsedResult(control_id, ident, birth_year, prog, None, raw.strip() or None, None, test, status,
                        cytology=cyto, flags=sorted(set(flags)), other_observations=others)


_COMPARATORS = ("<", ">", "<=", ">=", "<>")


def parse_hl7v2(raw: str) -> ParsedResult:
    # tolerate a UTF-8 byte-order mark and MLLP framing (VT ... FS CR) pasted from an interface engine
    text = raw.lstrip("\ufeff").strip().strip("\x0b\x1c").replace("\r\n", "\r").replace("\n", "\r").strip()
    if not text.startswith("MSH") or len(text) < 8:
        raise Invalid("not an HL7 v2 message (missing MSH)")
    # delimiters are declared by the message itself (MSH-1 and MSH-2); never assume '|' and '^'
    fs, comp, rep, esc = text[3], text[4], text[5], text[6]
    if len({fs, comp, rep, esc}) < 4 or any(ch.isalnum() or ch.isspace() for ch in (fs, comp, rep, esc)):
        raise Invalid("invalid HL7 delimiters in MSH-1/MSH-2")

    def unesc(v: str) -> str:
        if esc not in v:
            return v
        for code, ch in (("F", fs), ("S", comp), ("T", text[7] if len(text) > 7 else "&"), ("R", rep), ("E", esc)):
            v = v.replace(f"{esc}{code}{esc}", ch)
        return v

    segs = [s.split(fs) for s in text.split("\r") if s.strip()]
    msh = segs[0]
    msh[0] = "MSH"
    # MSH-1 is the field separator itself, so MSH-n is at index n-1
    msg_type, control_id = _field(msh, 8).replace(comp, "^"), unesc(_field(msh, 9))
    if not msg_type.startswith("ORU^R01"):
        raise Invalid(f"unsupported message type {msg_type or '?'} (expected ORU^R01)")
    digest = hashlib.sha256(text.encode()).hexdigest()
    if not control_id:  # never drop: derive a content-based id so the message is still idempotent and reviewable
        control_id = "NOID-" + digest[:16]
    pids = [s for s in segs if s[0] == "PID"]
    pid = pids[0] if pids else None
    ident = birth_year = None
    alt: list[str] = []
    if pid is not None:
        ids = [unesc(r.split(comp)[0]).strip() for r in _field(pid, 3).split(rep)]
        ids = [i for i in ids if i]
        ident, alt = (ids[0] if ids else None), ids[1:]
        dob = _field(pid, 7)
        birth_year = int(dob[:4]) if len(dob) >= 4 and dob[:4].isdigit() else None
        name = [unesc(x) for x in _field(pid, 5).split(rep)[0].split(comp)]
        xtn = _field(pid, 13).split(rep)[0].split(comp)
        phone = unesc(xtn[11] if len(xtn) > 11 and xtn[11] else xtn[0])
        who = {"family_name": _clip(name[0]), "given_name": _clip(" ".join(x for x in name[1:3] if x.strip())),
                "birth_date": _dob(dob, True), "gender": _HL7_SEX.get(_field(pid, 8).strip().upper()[:1]),
                "phone": _clip(phone, 30)}
    obs = []
    for obx in (s for s in segs if s[0] == "OBX"):
        code = _field(obx, 3).split(comp)[0]
        vtype = _field(obx, 2).upper()
        parts = [unesc(x) for x in _field(obx, 5).split(rep)[0].split(comp)]
        if vtype == "SN" or (len(parts) > 1 and parts[0].strip() in _COMPARATORS + ("=",)):
            # structured numeric: comparator^num1^separator^num2. A comparator ("<10", ">9.9") is NEVER dropped -
            # it is passed on as text and the clinical rules send it to human review
            cmp_, num = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
            val = num if cmp_ in ("", "=") and len(parts) <= 2 else "".join(p.strip() for p in parts)
        else:
            val = parts[1] if len(parts) > 1 and parts[1] else parts[0]
        obs.append((code, val, unesc(_field(obx, 6).split(comp)[0]) or None, _field(obx, 11) or "F", val))
    r = _combine(control_id, ident, birth_year, obs)
    r.alt_identifiers, r.payload_sha256 = alt, digest
    if pid is not None:
        r.demographics = {k: v for k, v in who.items() if v}
    obr = next((s for s in segs if s[0] == "OBR"), None)
    times = ([_field(obr, 7)] if obr is not None else []) + [_field(s, 14) for s in segs if s[0] == "OBX"]
    r.collected_at = next((t for t in (parse_collection_time(x, True) for x in times) if t is not None), None)
    if len(pids) > 1:
        return ParsedResult(control_id, ident, birth_year, None, None, None, payload_sha256=digest,
                            problem="more than one patient (PID segment) in one message - split per patient")
    if pid is None and r.problem is None:
        r.problem = "PID segment missing"
    return r


def parse_fhir_bundle(bundle: dict[str, Any]) -> ParsedResult:
    """Structurally broken JSON (wrong types where objects/lists are required) is rejected with a clear 4xx error,
    never a crash; a well-formed bundle with odd VALUES is kept and routed to human review."""
    if not isinstance(bundle, dict):
        raise Invalid("expected a FHIR Bundle object")
    try:
        return _parse_fhir_bundle(bundle)
    except Invalid:
        raise
    except (AttributeError, TypeError, KeyError, IndexError, ValueError) as e:
        raise Invalid(f"malformed FHIR Bundle structure ({type(e).__name__}): check entry/resource/identifier/"
                      "code/valueQuantity types") from e


def _parse_fhir_bundle(bundle: dict[str, Any]) -> ParsedResult:
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
    if pat is not None:
        nm = next(iter(pat.get("name") or []), {}) or {}
        tel = [t for t in (pat.get("telecom") or []) if isinstance(t, dict)]
        who = {"family_name": _clip(nm.get("family")), "given_name": _clip(" ".join(str(g) for g in (nm.get("given") or [])[:2])),
                "birth_date": _dob(pat.get("birthDate") or "", False),
                "gender": pat.get("gender") if pat.get("gender") in ("female", "male", "other", "unknown") else None,
                "phone": _clip(next((t.get("value") for t in tel if t.get("system") in ("phone", "sms")), None), 30),
                "email": _clip(next((t.get("value") for t in tel if t.get("system") == "email"), None), 200)}
        r.demographics = {k: v for k, v in who.items() if v}
    for o in (x for x in res if x.get("resourceType") == "Observation"):
        eff = o.get("effectiveDateTime") or (o.get("effectivePeriod") or {}).get("start")
        if (t := parse_collection_time(eff, False)) is not None:
            r.collected_at = t
            break
    r.payload_sha256 = hashlib.sha256(json.dumps(bundle, sort_keys=True, default=str).encode()).hexdigest()
    pats = [x for x in res if x.get("resourceType") == "Patient"]
    if len(pats) > 1:
        return ParsedResult(str(mid), ident, birth_year, None, None, None, payload_sha256=r.payload_sha256,
                            problem="more than one Patient in one Bundle - split per patient")
    if pat is None and r.problem is None:
        r.problem = "Bundle has no Patient"
    return r


def _match(db: Session, r: ParsedResult) -> tuple[Patient | None, str | None]:
    if not r.patient_identifier:
        return None, "no patient identifier"
    ids = [r.patient_identifier, *r.alt_identifiers]
    found = {p.id: p for p in db.scalars(select(Patient).where(Patient.mrn.in_(ids)))}
    if len(found) > 1:
        return None, "the message's patient identifiers point to different patients"
    if not found:  # no MRN match: an Emirates ID (PID-3 repetition) may identify the patient
        by_eid = {p.id: p for p in db.scalars(select(Patient).where(Patient.national_id.in_(ids)))}
        if len(by_eid) > 1:
            return None, "the Emirates ID belongs to more than one patient record (possible duplicate identity)"
        found = by_eid
    p = next(iter(found.values()), None)
    if p is None:
        return None, "identifier not found"
    if r.birth_year is not None and p.birth_year != r.birth_year:
        return None, "identifier found but birth year differs"
    return p, None


def _create(db: Session, p: Patient, r: ParsedResult, actor: Actor, now: datetime) -> tuple[int, Case | None]:
    lab = db.scalar(select(Organization).where(Organization.kind == "lab"))
    rep, case = wf.ingest_result(db, p, r.programme, value=r.value, value_text=r.value_text, unit=r.unit, test=r.test,
                                 status=r.status, cytology=r.cytology,
                                 performer_org_id=lab.id if lab else p.managing_org_id, actor=actor, now=now,
                                 extra_reasons=list(r.flags) or None, other_observations=r.other_observations or None)
    if case is not None and case.tier not in CLINICIAN_CONFIRMED_TIERS and S(case.state) == S.ISSUED \
            and not case.review_required:
        wf.transition(db, case, S.TRIAGED, SYSTEM, now=now, note="auto-triaged by deterministic programme rule")
    return rep.id, case


def ingest(db: Session, source: str, r: ParsedResult, actor: Actor, now: datetime | None = None) -> IngestMessage:
    now = now or utcnow()
    prior = db.scalar(select(IngestMessage).where(IngestMessage.message_id == r.message_id))
    if prior is not None and (prior.payload_sha256 is None or r.payload_sha256 is None
                              or prior.payload_sha256 == r.payload_sha256):
        audit.record(db, actor, "E", "ingest.duplicate", entity_type="IngestMessage", entity_id=prior.id, ts=now,
                     detail={"message_id": r.message_id})
        return prior
    if prior is not None:
        # same control ID, DIFFERENT content: never silently treat it as a replay (it may be a corrected result)
        conflict_id = f"{r.message_id[:100]}#conflict-{r.payload_sha256[:12]}"
        again = db.scalar(select(IngestMessage).where(IngestMessage.message_id == conflict_id))
        if again is not None:
            return again
        r.message_id = conflict_id
        # processed as a result (it may be a correction) but always reviewed by a clinician, never auto-actioned
        r.flags = sorted({*r.flags, "control_id_reused"})
    if r.collected_at is not None:  # specimen timing: never silently act on a months-old or "future" specimen
        if r.collected_at > now + FUTURE_TOLERANCE:
            r.flags = sorted({*r.flags, "collection_date_in_future"})
        elif now - r.collected_at > timedelta(days=STALE_RESULT_DAYS):
            r.flags = sorted({*r.flags, "stale_result"})
    msg = IngestMessage(source=source, message_id=r.message_id, received_at=now, patient_identifier=r.patient_identifier,
                        birth_year=r.birth_year, programme=r.programme, value=r.value,
                        value_text=(r.value_text or "")[:60] or None, unit=(r.unit or "")[:30] or None, test=r.test,
                        result_status=r.status, cytology=(r.cytology or "")[:30] or None, status="processed",
                        payload_sha256=r.payload_sha256, flags=list(r.flags) or None,
                        alt_identifiers=list(r.alt_identifiers) or None,
                        other_observations=r.other_observations or None,
                        patient_demographics=dict(r.demographics) or None)
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
                     msg.unit, msg.test, msg.result_status or "final", msg.cytology, flags=list(msg.flags or []),
                     other_observations=list(msg.other_observations or []))
    msg.report_id, case = _create(db, p, r, actor, now)
    msg.case_id = case.id if case else None
    msg.status = "resolved"
    msg.resolved_by_user_id = actor.user_id
    audit.record(db, actor, "U", "ingest.match_resolved", entity_type="IngestMessage", entity_id=msg.id,
                 case_id=msg.case_id, ts=now, detail={"patient_id": p.id})
    return msg


def ack_hl7(msg: IngestMessage | None, control_id: str, error: str | None = None) -> str:
    """Minimal HL7 v2 ACK (AA = accepted, AE = application error)."""
    ts = utcnow().strftime("%Y%m%d%H%M%S") + "+0000"
    code = "AE" if error or (msg and msg.status in ("needs_match_review", "needs_review")) else "AA"
    text = error or (msg.error if msg and msg.error else ("accepted" if msg else ""))
    return "\r".join([f"MSH|^~\\&|KARESHAFAA|PROGRAMME|LAB|LAB|{ts}||ACK^R01|ACK{control_id}|P|2.5.1",
                      f"MSA|{code}|{control_id}|{text}"])
