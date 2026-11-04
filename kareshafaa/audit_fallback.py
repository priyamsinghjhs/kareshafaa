"""Durable fallback for request-level audit events that could not be written to the hash-chained trail.

Why: the per-request audit event is written AFTER the request's own transaction has committed (main.py,
audit_every_request). If that write fails for a state-changing request, the change is already saved, so the user
still gets their answer - but the event must not be lost. The middleware retries once, then hands the event(s) here:

1. ``store`` writes them to the ``audit_fallback_events`` table (plain Core insert: it does not take the audit
   writer lock, so it still works when the audit writer is the part that is stuck);
2. if the database itself is unreachable, each event is appended as one JSON line to the append-only file
   ``DATA_DIR/audit-fallback.jsonl`` (fsync'd; the file is never rewritten or truncated by the app);
3. if even that fails, the event is written to the error log in full (last resort) and counted in memory.

``reconcile`` (run by the timed-jobs worker after each SLA sweep, and by an admin from Admin > System) first imports
JSONL lines not yet in the table (keyed by ``fallback_id``, so re-running is harmless), then appends every
unreconciled event to the hash-chained ``audit_events`` trail in original-time order, keeping its ORIGINAL
timestamp (``ts``) and adding ``detail.reconciled_late = true`` plus when it was stored and reconciled. Chain
position (id order) therefore shows when the event entered the trail; ``ts`` shows when it happened.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update

log = logging.getLogger("kareshafaa.audit")
FILE_NAME = "audit-fallback.jsonl"
_FILE_LOCK = threading.Lock()
LOST_TO_LOG = {"count": 0}  # events that reached neither the table nor the file (only the error log)


def _path():
    from .config import get_settings
    return get_settings().data_dir / FILE_NAME


def _row(actor, action: str, event: str, kw: dict, error: str, now: datetime) -> dict:
    ts = kw.get("ts") or now
    eid = kw.get("entity_id")
    return {"fallback_id": str(uuid.uuid4()), "ts": ts, "actor_user_id": actor.user_id, "actor_role": actor.role,
            "actor_display": actor.display, "action": action, "event": event,
            "entity_type": kw.get("entity_type"), "entity_id": None if eid is None else str(eid),
            "case_id": kw.get("case_id"), "outcome": kw.get("outcome", "success"), "path": kw.get("path"),
            "detail": kw.get("detail"), "error": error[:300], "stored_at": now}


def store(items: list, error: BaseException | str, prefer: str = "table") -> str:
    """Keep events whose audit write failed. Returns where they went: 'table', 'file' or 'log'. Never raises.
    prefer="file" (reads waiting for their response): the fsync'd file first, so no database lock is waited for;
    the table is then only the second choice."""
    from .models import AuditFallbackEvent, utcnow

    now = utcnow()
    err = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    rows = [_row(a, act, ev, dict(kw), err, now) for a, act, ev, kw in items]
    if not rows:
        return "table"
    if prefer == "file" and _append_file(rows, err, None):
        return "file"
    try:
        from .db import get_engine
        with get_engine().begin() as conn:
            conn.execute(insert(AuditFallbackEvent.__table__), [dict(r, stored_in="table") for r in rows])
        log.error("request audit write failed (%s); %d event(s) kept in audit_fallback_events for reconciliation",
                  err[:120], len(rows))
        return "table"
    except Exception as e:  # noqa: BLE001 - database down: fall through to the file
        db_err = e
    if prefer != "file" and _append_file(rows, err, db_err):
        return "file"
    LOST_TO_LOG["count"] += len(rows)
    log.critical("AUDIT EVENT NOT STORED (table and file both failed) - full event follows for manual "
                 "reconciliation: %s", json.dumps(rows, default=_json_default))
    return "log"


def _append_file(rows: list[dict], err: str, db_err: BaseException | None) -> bool:
    try:
        with _FILE_LOCK:
            p = _path()
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                data = "".join(json.dumps(r, default=_json_default, separators=(",", ":")) + "\n" for r in rows)
                os.write(fd, data.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        log.error("request audit write failed (%s)%s; %d event(s) appended to %s for reconciliation", err[:120],
                  f" and the fallback table was unavailable ({type(db_err).__name__})" if db_err else "", len(rows),
                  FILE_NAME)
        return True
    except Exception:  # noqa: BLE001
        log.warning("audit fallback file not writable", exc_info=True)
        return False


def _json_default(v: Any):
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _file_rows() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    out = []
    with _FILE_LOCK, open(p, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                r["ts"] = datetime.fromisoformat(r["ts"])
                r["stored_at"] = datetime.fromisoformat(r["stored_at"])
                out.append(r)
            except Exception:  # noqa: BLE001 - a torn last line (crash mid-write) is skipped, never fatal
                log.warning("%s line %d is not a complete event; skipped", FILE_NAME, n)
    return out


def import_file(db) -> int:
    """Copy JSONL events not yet in the table into it (idempotent: keyed by fallback_id). Returns rows added."""
    from .models import AuditFallbackEvent

    rows = _file_rows()
    if not rows:
        return 0
    have = set(db.scalars(select(AuditFallbackEvent.fallback_id)
                          .where(AuditFallbackEvent.fallback_id.in_([r["fallback_id"] for r in rows]))))
    new = [dict(r, stored_in="file") for r in rows if r["fallback_id"] not in have]
    if new:
        db.execute(insert(AuditFallbackEvent.__table__), new)
    return len(new)


def status(db) -> dict:
    """Unreconciled events (table + file lines not yet imported) for the admin System page and /healthz."""
    from .models import AuditFallbackEvent

    in_table = db.scalar(select(func.count(AuditFallbackEvent.id)).where(AuditFallbackEvent.reconciled_at.is_(None))) or 0
    oldest = db.scalar(select(func.min(AuditFallbackEvent.ts)).where(AuditFallbackEvent.reconciled_at.is_(None)))
    rows = _file_rows()
    in_file = 0
    if rows:
        known = set(db.scalars(select(AuditFallbackEvent.fallback_id)
                               .where(AuditFallbackEvent.fallback_id.in_([r["fallback_id"] for r in rows]))))
        pending = [r for r in rows if r["fallback_id"] not in known]
        in_file = len(pending)
        if pending:
            o = min(r["ts"] for r in pending)
            oldest = o if oldest is None else min(oldest, o)
    last = db.scalar(select(func.max(AuditFallbackEvent.reconciled_at)))
    return {"unreconciled": in_table + in_file, "in_table": in_table, "in_file": in_file,
            "oldest_at": oldest.isoformat() if oldest else None, "last_reconciled_at": last.isoformat() if last else None,
            "not_stored": LOST_TO_LOG["count"]}


def reconcile(db, actor=None, limit: int = 5000) -> dict:
    """Append unreconciled fallback events to the hash-chained audit trail (original timestamps, marked
    reconciled-late) in ONE transaction with marking them reconciled, so an event is never appended twice."""
    from . import audit
    from .models import AuditFallbackEvent, utcnow
    from .security import SYSTEM, Actor

    now = utcnow()
    imported = import_file(db)
    rows = list(db.scalars(select(AuditFallbackEvent).where(AuditFallbackEvent.reconciled_at.is_(None))
                           .order_by(AuditFallbackEvent.ts, AuditFallbackEvent.id).limit(limit)))
    for r in rows:
        who = Actor(user_id=r.actor_user_id, role=r.actor_role, display=r.actor_display,
                    is_human=r.actor_role not in ("system", "ml_service", "anonymous"))
        detail = dict(r.detail or {})
        detail.update({"reconciled_late": True, "fallback_id": r.fallback_id, "stored_at": r.stored_at.isoformat(),
                       "stored_in": r.stored_in, "reconciled_at": now.isoformat(), "write_error": r.error})
        ev = audit.record(db, who, r.action, r.event, entity_type=r.entity_type, entity_id=r.entity_id,
                          case_id=r.case_id, outcome=r.outcome, path=r.path, detail=detail, ts=r.ts)
        db.execute(update(AuditFallbackEvent).where(AuditFallbackEvent.id == r.id)
                   .values(reconciled_at=now, audit_event_id=ev.id))
    if rows or imported:
        audit.record(db, actor or SYSTEM, "E", "audit.fallback_reconciled", entity_type="AuditEvent", ts=now,
                     detail={"reconciled": len(rows), "imported_from_file": imported,
                             "oldest": rows[0].ts.isoformat() if rows else None})
    db.commit()
    left = status(db)["unreconciled"]
    if rows:
        log.warning("audit fallback: %d event(s) appended to the audit trail late (original times kept); %d left",
                    len(rows), left)
    return {"reconciled": len(rows), "imported_from_file": imported, "remaining": left}
