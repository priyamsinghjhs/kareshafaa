"""Care coordinators: appointment requests from the patient app (screening, next screening from a recall, follow-up
times). Listed in the work queue with their answer-by time; confirmed (booked) or declined with a reason."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..calendar_uae import as_utc_naive
from ..db import get_db
from ..security import Actor
from ..services import patient_requests as pr
from . import schemas as sch
from .deps import require

router = APIRouter(prefix="/api/patient-requests", tags=["patient requests"])


@router.get("")
def list_requests(status: str = Query("open", pattern="^(open|confirmed|declined|withdrawn|all)$"),
                  programme: str | None = Query(None, max_length=40), actor: Actor = Depends(require("queue:read")),
                  db: Session = Depends(get_db)) -> dict:
    return pr.work_list(db, actor, status, programme)


@router.post("/{request_id}/confirm")
def confirm_request(request_id: int, body: sch.RequestConfirmIn, actor: Actor = Depends(require("appointment:write")),
                    db: Session = Depends(get_db)) -> dict:
    r = pr.confirm(db, actor, request_id, start=as_utc_naive(body.start) if body.start else None, org_id=body.org_id,
                   note=body.note, beyond_target_reason=body.beyond_target_reason)
    db.commit()
    return {"ok": True, "status": r.status, "appointment_id": r.appointment_id}


@router.post("/{request_id}/decline")
def decline_request(request_id: int, body: sch.RequestDeclineIn, actor: Actor = Depends(require("appointment:write")),
                    db: Session = Depends(get_db)) -> dict:
    r = pr.decline(db, actor, request_id, body.reason_code, body.note)
    db.commit()
    return {"ok": True, "status": r.status}
