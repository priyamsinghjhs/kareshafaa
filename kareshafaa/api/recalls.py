"""Recall list for coordinators and GPs (who is due for their next screen), invitations and cancellations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import Actor
from ..services import recall as svc
from ..services.errors import DomainError, Forbidden
from . import schemas as sch
from .deps import current_actor, require

router = APIRouter(prefix="/api/recalls", tags=["recall"])


class BulkInviteIn(sch._In):
    ids: list[int] = Field(min_length=1, max_length=50)


def _reader(actor: Actor = Depends(current_actor)) -> Actor:
    if not (actor.can("recall:manage") or actor.can("recall:read")):
        raise Forbidden("role cannot see recalls")
    return actor


@router.get("")
def list_recalls(view: str = Query("due", pattern="^(due|overdue|invited|upcoming|completed)$"),
                 programme: str | None = Query(None, max_length=40), q: str | None = Query(None, max_length=80),
                 actor: Actor = Depends(_reader), db: Session = Depends(get_db)) -> dict:
    return svc.list_recalls(db, actor, view=view, programme=programme or None, q=q)


@router.get("/summary")
def summary(actor: Actor = Depends(_reader), db: Session = Depends(get_db)) -> dict:
    return svc.uptake(db, actor=actor)


@router.post("/{recall_id}/invite")
def invite(recall_id: int, actor: Actor = Depends(require("recall:manage")), db: Session = Depends(get_db)) -> dict:
    r, p = svc.get_recall(db, actor, recall_id)
    com = svc.send_invite(db, actor, r, p)
    db.commit()
    return {"ok": True, "channel": com.channel, "message": com.content, "recall": svc.row(db, r, p, svc.utcnow().date())}


@router.post("/invite")
def invite_many(body: BulkInviteIn, actor: Actor = Depends(require("recall:manage")), db: Session = Depends(get_db)) -> dict:
    sent, failed = [], []
    for rid in body.ids:
        try:
            r, p = svc.get_recall(db, actor, rid)
            com = svc.send_invite(db, actor, r, p)
            db.flush()
            sent.append({"id": rid, "channel": com.channel})
        except DomainError as e:  # no channel, or invited in the last 24 hours (Invalid): must not block the others
            failed.append({"id": rid, "reason": e.message})
    db.commit()
    return {"ok": True, "sent": sent, "failed": failed}


@router.post("/{recall_id}/cancel")
def cancel(recall_id: int, body: sch.ReasonIn, actor: Actor = Depends(require("recall:manage")),
           db: Session = Depends(get_db)) -> dict:
    r, p = svc.get_recall(db, actor, recall_id)
    svc.cancel(db, actor, r, body.reason)
    db.commit()
    return {"ok": True, "recall": svc.row(db, r, p, svc.utcnow().date())}
