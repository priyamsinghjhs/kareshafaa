"""Care summaries to the registered GP: inbox, one summary, acknowledge."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import utcnow
from ..security import Actor
from ..services import gp_summary as svc
from . import schemas as sch
from .deps import current_actor, require

router = APIRouter(prefix="/api/gp-summaries", tags=["gp-summary"])


class AckIn(sch._In):
    note: str | None = Field(default=None, max_length=1000)


@router.get("")
def inbox(view: str = Query("unread", pattern="^(unread|all)$"), actor: Actor = Depends(require("summary:ack")),
          db: Session = Depends(get_db)) -> dict:
    return svc.inbox(db, actor, view)


@router.get("/{sid}")
def one(sid: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.row(db, svc.get(db, actor, sid), utcnow(), full=True)


@router.post("/{sid}/ack")
def ack(sid: int, body: AckIn, actor: Actor = Depends(require("summary:ack")), db: Session = Depends(get_db)) -> dict:
    s = svc.acknowledge(db, actor, svc.get(db, actor, sid), body.note)
    db.commit()
    return {"ok": True, "summary": svc.row(db, s, utcnow(), full=True)}
