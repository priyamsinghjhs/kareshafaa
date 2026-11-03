"""Sign-in help requests from the sign-in page: patient password resets (care coordinators) and staff requests
(system administrators). The public pages are server-rendered forms in main.py (/forgot-username,
/forgot-password, /reset-password)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import Actor
from ..services import signin_help as sh
from . import schemas as sch
from .deps import current_actor

router = APIRouter(prefix="/api/signin-help", tags=["sign-in help"])


@router.get("")
def list_requests(audience: str = Query(..., pattern="^(patient|staff)$"),
                  status: str = Query("open", pattern="^(open|done|closed|all)$"),
                  actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return sh.list_requests(db, actor, audience, status)


@router.post("/{request_id}/close")
def close_request(request_id: int, body: sch.SigninHelpCloseIn, actor: Actor = Depends(current_actor),
                  db: Session = Depends(get_db)) -> dict:
    r = sh.close_request(db, actor, request_id, body.outcome, body.note)
    db.commit()
    return {"ok": True, "status": r.status}
