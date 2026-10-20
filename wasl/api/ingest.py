"""Integration endpoints: HL7 v2 ORU^R01 and FHIR R4 Bundle result ingestion + match-review queue."""
from __future__ import annotations

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
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
    out = {"ok": msg.status in ("processed", "resolved"), "message_id": msg.message_id, "status": msg.status,
           "reason": msg.error, "case": case_summary(db, c) if c else None, "actionable": c is not None}
    if ack is not None:
        out["ack"] = ack
    return out


@router.post("/hl7v2")
async def hl7v2(request: Request, actor: Actor = Depends(require("case:create")), db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise Invalid("message too large")
    try:
        parsed = ing.parse_hl7v2(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as e:
        raise Invalid("message must be UTF-8") from e
    def work() -> dict:  # sync DB work off the event loop
        msg = ing.ingest(db, "hl7v2", parsed, actor)
        db.commit()
        return _out(db, msg, ing.ack_hl7(msg, parsed.message_id))
    return await run_in_threadpool(work)


@router.post("/fhir")
async def fhir_bundle(request: Request, actor: Actor = Depends(require("case:create")), db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise Invalid("bundle too large")
    import json

    try:
        bundle = json.loads(raw)
    except ValueError as e:
        raise Invalid("body must be JSON") from e
    if not isinstance(bundle, dict):
        raise Invalid("body must be a FHIR Bundle object")
    def work() -> dict:
        msg = ing.ingest(db, "fhir", ing.parse_fhir_bundle(bundle), actor)
        db.commit()
        return _out(db, msg)
    return await run_in_threadpool(work)


@router.get("/sample", response_class=PlainTextResponse)
def sample(actor: Actor = Depends(require("case:create")), db: Session = Depends(get_db)) -> str:
    """A ready-to-ingest synthetic ORU^R01 (positive FIT) for a real synthetic patient without an open FIT case."""
    from ..models import Patient, utcnow

    open_fit = select(Case.patient_id).where(Case.programme == "colorectal_fit", Case.state != "closed")
    p = db.scalar(select(Patient).where(Patient.birth_year <= utcnow().year - 45, Patient.id.not_in(open_fit),
                                        Patient.deceased.is_(False)).order_by(func.random()).limit(1))
    if p is None:
        raise Invalid("no eligible synthetic patient")
    ts = utcnow().strftime("%Y%m%d%H%M%S")
    return "\r".join([
        f"MSH|^~\\&|LIS|CENTRAL_SCREENING_LAB|WASL|EMIRATE_SCREENING|{ts}||ORU^R01^ORU_R01|LAB-{ts}-{p.id}|P|2.5.1",
        f"PID|1||{p.mrn}^^^WASL^MR||{p.family_name.upper()}^{p.given_name.upper()}||{p.birth_year}0101|{p.gender[0].upper()}",
        f"OBR|1|ORD-{p.id}|FIL-{ts[-6:]}|29771-3^FIT haemoglobin^LN|||{ts}|||||||||||||||{ts}|||F",
        "OBX|1|NM|29771-3^FIT haemoglobin^LN||{v}|ug/g|<120|H|||F".format(v=180 + (p.id % 200)),
    ])


@router.get("/messages")
def messages(status: str | None = None, actor: Actor = Depends(require("case:create")), db: Session = Depends(get_db)) -> dict:
    q = select(IngestMessage).order_by(IngestMessage.id.desc()).limit(200)
    if status:
        q = q.where(IngestMessage.status == status)
    return {"items": [{"id": m.id, "source": m.source, "message_id": m.message_id, "status": m.status,
                       "received_at": m.received_at.isoformat(), "patient_identifier": m.patient_identifier,
                       "programme": m.programme, "reason": m.error, "case_id": m.case_id} for m in db.scalars(q)]}


@router.post("/messages/{msg_id}/resolve")
def resolve(msg_id: int, body: ResolveIn, actor: Actor = Depends(require("case:create")), db: Session = Depends(get_db)) -> dict:
    msg = ing.resolve_match(db, msg_id, body.patient_id, actor)
    db.commit()
    return _out(db, msg)
