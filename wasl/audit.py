"""Append-only, hash-chained audit trail (FHIR AuditEvent)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, utcnow
from .security import Actor

GENESIS = "0" * 64


def _digest(prev: str, ts: datetime, actor: str, action: str, event: str, entity: str, detail: Any) -> str:
    payload = json.dumps([prev, ts.isoformat(), actor, action, event, entity, detail], sort_keys=True,
                         default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def record(db: Session, actor: Actor, action: str, event: str, *, entity_type: str | None = None,
           entity_id: Any = None, case_id: int | None = None, outcome: str = "success",
           path: str | None = None, detail: dict | None = None, ts: datetime | None = None) -> AuditEvent:
    """Append one audit event in the caller's transaction (so domain writes and their audit are atomic)."""
    ts = ts or utcnow()
    last = db.execute(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)).scalar()
    prev = last or GENESIS
    eid = None if entity_id is None else str(entity_id)
    actor_key = f"{actor.role}:{actor.user_id}"
    ev = AuditEvent(ts=ts, actor_user_id=actor.user_id, actor_role=actor.role, actor_display=actor.display,
                    action=action, event=event, entity_type=entity_type, entity_id=eid, case_id=case_id,
                    outcome=outcome, path=path, detail=detail, prev_hash=prev,
                    hash=_digest(prev, ts, actor_key, action, event, f"{entity_type}/{eid}", detail))
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session) -> dict:
    prev = GENESIS
    n = 0
    for ev in db.scalars(select(AuditEvent).order_by(AuditEvent.id)).yield_per(2000):
        expect = _digest(prev, ev.ts, f"{ev.actor_role}:{ev.actor_user_id}", ev.action, ev.event,
                         f"{ev.entity_type}/{ev.entity_id}", ev.detail)
        if ev.prev_hash != prev or ev.hash != expect:
            return {"valid": False, "broken_at_id": ev.id, "checked": n}
        prev = ev.hash
        n += 1
    return {"valid": True, "checked": n, "head": prev}
