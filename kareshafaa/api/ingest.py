"""Integration endpoints: HL7 v2 ORU^R01 and FHIR R4 Bundle result ingestion + match-review queue."""
from __future__ import annotations

import copy

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Case, IngestMessage
from ..security import Actor
from ..services import ingest as ing
from ..services.errors import Invalid
from .deps import require
from .serializers import case_summary

router = APIRouter(prefix="/api/ingest", tags=["integration"])
MAX_BYTES = 64 * 1024


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: int


def _out(db: Session, msg: IngestMessage, ack: str | None = None) -> dict:
    c = db.get(Case, msg.case_id) if msg.case_id else None
    out = {"ok": msg.status in ("processed", "resolved"), "id": msg.id, "message_id": msg.message_id, "status": msg.status,
           "reason": msg.error, "case": case_summary(db, c) if c else None, "actionable": c is not None}
    if ack is not None:
        out["ack"] = ack
    return out


def _ingest_retrying(db: Session, source: str, parsed: ing.ParsedResult, actor: Actor) -> IngestMessage:
    """Concurrent deliveries of the same message (or two results for one patient at once) collide on a unique
    index; the loser rolls back and re-runs, then sees the winner's row (idempotent replay / linked to the open
    case) instead of failing with a 500 or creating a duplicate."""
    for attempt in range(3):
        try:
            msg = ing.ingest(db, source, copy.deepcopy(parsed), actor)
            db.commit()
            return msg
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


@router.post("/hl7v2")
async def hl7v2(request: Request, actor: Actor = Depends(require("results:inbox")), db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise Invalid("message too large")
    try:
        parsed = ing.parse_hl7v2(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as e:
        raise Invalid("message must be UTF-8") from e
    def work() -> dict:  # sync DB work off the event loop
        msg = _ingest_retrying(db, "hl7v2", parsed, actor)
        return _out(db, msg, ing.ack_hl7(msg, parsed.message_id))
    return await run_in_threadpool(work)


@router.post("/fhir")
async def fhir_bundle(request: Request, actor: Actor = Depends(require("results:inbox")), db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise Invalid("bundle too large")
    import json

    try:
        bundle = json.loads(raw)
    except (ValueError, RecursionError) as e:  # RecursionError: deeply nested JSON (was a 500)
        raise Invalid("body must be JSON (and not nested absurdly deep)") from e
    if not isinstance(bundle, dict):
        raise Invalid("body must be a FHIR Bundle object")
    def work() -> dict:
        msg = _ingest_retrying(db, "fhir", ing.parse_fhir_bundle(bundle), actor)
        return _out(db, msg)
    return await run_in_threadpool(work)


@router.post("/fhir-history")
async def fhir_history(request: Request, actor: Actor = Depends(require("record:write")),
                       db: Session = Depends(get_db)) -> dict:
    """Import a patient's clinical history (Encounter, MedicationRequest/Statement, AllergyIntolerance, Condition,
    non-programme Observation) from a FHIR R4 Bundle into an EXISTING patient record. Idempotent per resource id."""
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise Invalid("bundle too large")
    import json

    try:
        bundle = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise Invalid("body must be JSON (and not nested absurdly deep)") from e
    from ..services.clinical_record import import_fhir_history

    def work() -> dict:
        try:
            out = import_fhir_history(db, bundle, actor)
        except (AttributeError, TypeError, KeyError, IndexError) as e:
            raise Invalid(f"malformed FHIR Bundle structure ({type(e).__name__})") from e
        db.commit()
        return {"ok": True, **out}
    return await run_in_threadpool(work)


@router.get("/sample", response_class=PlainTextResponse)
def sample(actor: Actor = Depends(require("results:inbox")), db: Session = Depends(get_db)) -> str:
    """A ready-to-ingest synthetic ORU^R01 (positive FIT) for a real synthetic patient without an open FIT case."""
    from ..models import Patient, utcnow

    open_fit = select(Case.patient_id).where(Case.programme == "colorectal_fit", Case.state != "closed")
    p = db.scalar(select(Patient).where(Patient.birth_year <= utcnow().year - 45, Patient.id.not_in(open_fit),
                                        Patient.deceased.is_(False)).order_by(func.random()).limit(1))
    if p is None:
        raise Invalid("no eligible patient record for a sample message")
    ts = utcnow().strftime("%Y%m%d%H%M%S") + "+0000"
    return "\r".join([
        f"MSH|^~\\&|LIS|CENTRAL_SCREENING_LAB|KARESHAFAA|EMIRATE_SCREENING|{ts}||ORU^R01^ORU_R01|LAB-{ts}-{p.id}|P|2.5.1",
        f"PID|1||{p.mrn}^^^KARESHAFAA^MR||{p.family_name.upper()}^{p.given_name.upper()}||{p.birth_year}0101|{p.gender[0].upper()}",
        f"PV1|1|O|SCREEN^^^{_site(db, p)}",
        f"ORC|RE|ORD-{p.id}|FIL-{ts[-6:]}|||||||||{_orderer(db, p)}",
        f"OBR|1|ORD-{p.id}|FIL-{ts[-6:]}|29771-3^FIT haemoglobin^LN|||{ts}|||||||||||||||{ts}|||F",
        "OBX|1|NM|29771-3^FIT haemoglobin^LN||{v}|ug/g|<120|H|||F".format(v=180 + (p.id % 200)),
    ])


def _site(db: Session, p) -> str:
    from ..models import Organization

    o = db.get(Organization, p.managing_org_id)
    return (o.name if o else "Screening clinic").replace("|", " ").replace("^", " ")


def _orderer(db: Session, p) -> str:
    from ..models import Practitioner

    g = db.get(Practitioner, p.gp_id) if p.gp_id else None
    if g is None:
        return ""
    parts = g.name.replace("Dr ", "").replace("Dr. ", "").split()
    return f"{g.id}^{parts[-1] if parts else ''}^{' '.join(parts[:-1])}^^^Dr".replace("|", " ")


@router.get("/messages")
def messages(status: str | None = None, actor: Actor = Depends(require("results:inbox")), db: Session = Depends(get_db)) -> dict:
    q = select(IngestMessage).order_by(IngestMessage.id.desc()).limit(200)
    if status:
        q = q.where(IngestMessage.status == status)
    return {"items": [{"id": m.id, "source": m.source, "message_id": m.message_id, "status": m.status,
                       "received_at": m.received_at.isoformat(), "patient_identifier": m.patient_identifier,
                       "programme": m.programme, "reason": m.error, "case_id": m.case_id,
                       "ordering_clinician": m.ordering_clinician, "screening_site": m.screening_site,
                       "specimen_collected_at": m.specimen_collected_at.isoformat() if m.specimen_collected_at else None,
                       **_who(m)} for m in db.scalars(q)]}


def _who(m: IngestMessage) -> dict:
    """Name / date of birth from the lab message - shown only while it waits for a match, to help find the person."""
    d = (m.patient_demographics or {}) if m.status == "needs_match_review" else {}
    name = " ".join(x for x in (d.get("given_name"), d.get("family_name")) if x)
    return {"patient_name": name or None, "patient_birth_date": d.get("birth_date")}


@router.post("/messages/{msg_id}/resolve")
def resolve(msg_id: int, body: ResolveIn, actor: Actor = Depends(require("results:inbox")), db: Session = Depends(get_db)) -> dict:
    msg = ing.resolve_match(db, msg_id, body.patient_id, actor)
    db.commit()
    return _out(db, msg)
