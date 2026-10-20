"""Append-only, hash-chained audit trail (FHIR AuditEvent)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import event, select, text
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
    """Append one audit event in the caller's transaction (so domain writes and their audit are atomic).

    The hash chain is computed at flush time while holding the writer lock (see _chain_on_flush), so
    concurrent requests can never fork the chain."""
    ts = ts or utcnow()
    eid = None if entity_id is None else str(entity_id)
    ev = AuditEvent(ts=ts, actor_user_id=actor.user_id, actor_role=actor.role, actor_display=actor.display,
                    action=action, event=event, entity_type=entity_type, entity_id=eid, case_id=case_id,
                    outcome=outcome, path=path, detail=detail, prev_hash="", hash="")
    db.add(ev)
    db.info.setdefault("audit_pending", []).append(ev)
    db.flush()
    return ev


# --- writer serialisation ---------------------------------------------------------------------------
# One process-wide lock is taken at the first flush that writes anything in a transaction and released
# when the transaction ends. Taking it before any database write lock avoids lock-order deadlocks, and it
# makes "read chain head -> insert next link" atomic. On PostgreSQL a transaction-scoped advisory lock
# extends the same guarantee across processes/nodes; on SQLite an exclusive flock() on a lock file in
# DATA_DIR does the same across uvicorn worker processes on one host.
class _WriterLock:
    """Re-entrant per thread, but releasable from any thread (FastAPI may close a session in a different
    worker thread from the one that flushed). The cross-process file lock is held while count > 0."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._owner: int | None = None
        self._count = 0

    def acquire(self, file_lock: bool) -> None:
        me = threading.get_ident()
        with self._cond:
            while self._count and self._owner != me:
                self._cond.wait()
            first = self._count == 0
            self._owner = me
            self._count += 1
        if first and file_lock:
            _file_lock(True)

    def release(self, file_lock: bool) -> None:
        with self._cond:
            self._count -= 1
            last = self._count == 0
            if last and file_lock:
                _file_lock(False)
            if last:
                self._owner = None
                self._cond.notify_all()


_WRITER_LOCK = _WriterLock()
_FILE_LOCK_FD: int | None = None


def _file_lock(acquire: bool) -> None:
    global _FILE_LOCK_FD
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX: single process only
        return
    if _FILE_LOCK_FD is None:
        from .config import get_settings
        d = get_settings().data_dir
        d.mkdir(parents=True, exist_ok=True)
        _FILE_LOCK_FD = os.open(str(d / ".audit-writer.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(_FILE_LOCK_FD, fcntl.LOCK_EX if acquire else fcntl.LOCK_UN)


@event.listens_for(Session, "before_flush")
def _chain_on_flush(session: Session, _ctx, _instances) -> None:
    if not (session.new or session.dirty or session.deleted):
        return
    if not session.info.get("writer_lock"):
        is_pg = session.get_bind().dialect.name == "postgresql"
        _WRITER_LOCK.acquire(file_lock=not is_pg)
        session.info["writer_lock"] = "pg" if is_pg else "file"
        if is_pg:
            session.execute(text("SELECT pg_advisory_xact_lock(4242)"))
    pending = [e for e in session.info.get("audit_pending", []) if e in session.new]
    if not pending:
        return
    head = session.info.get("audit_head")
    if head is None:
        head = session.execute(select(AuditEvent.hash).where(AuditEvent.hash != "")
                               .order_by(AuditEvent.id.desc()).limit(1)).scalar() or GENESIS
    for ev in pending:
        ev.prev_hash = head
        ev.hash = _digest(head, ev.ts, f"{ev.actor_role}:{ev.actor_user_id}", ev.action, ev.event,
                          f"{ev.entity_type}/{ev.entity_id}", ev.detail)
        head = ev.hash
    session.info["audit_head"] = head
    session.info["audit_pending"] = []


def _release(session: Session, *_a) -> None:
    session.info.pop("audit_head", None)
    session.info.pop("audit_pending", None)
    kind = session.info.pop("writer_lock", None)
    if kind:
        _WRITER_LOCK.release(file_lock=kind == "file")


def _on_tx_end(session: Session, transaction) -> None:
    if transaction.parent is None:  # outermost transaction ended (commit, rollback or close)
        _release(session)


event.listen(Session, "after_commit", _release)
event.listen(Session, "after_rollback", _release)
event.listen(Session, "after_transaction_end", _on_tx_end)


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
