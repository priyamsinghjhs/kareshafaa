"""Background job health for Admin -> System monitoring (free: stored in the app's own database, no paid APM).

``track(name, fn)`` runs a timed job and records start, finish, success or failure (exception class name only),
duration and a small result summary in ``background_job_status``. Recording never breaks the job: if the status
write itself fails it is logged and the job's own result stands."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from sqlalchemy import select

from ..models import BackgroundJobStatus, utcnow

log = logging.getLogger("kareshafaa.jobs")
JOBS = {"sla_sweep": "Deadline check (SLA sweep)", "retention_purge": "Retention purge",
        "audit_reconcile": "Audit fallback reconciliation", "result_reconcile": "Result-to-case reconciliation"}


def _summary(out: Any) -> dict | None:
    if not isinstance(out, dict):
        return None
    return {k: v for k, v in out.items() if isinstance(v, (int, float, str, bool)) or v is None}


def _write(name: str, **fields) -> None:
    from ..db import session_scope
    try:
        with session_scope() as db:
            row = db.get(BackgroundJobStatus, name)
            if row is None:
                row = BackgroundJobStatus(name=name, runs=0, failures=0)
                db.add(row)
            for k, v in fields.items():
                if k in ("runs", "failures"):
                    setattr(row, k, (getattr(row, k) or 0) + v)
                else:
                    setattr(row, k, v)
    except Exception:  # noqa: BLE001 - monitoring must never break the job
        log.warning("could not record job status for %s", name, exc_info=True)


def track(name: str, fn: Callable[[], Any]) -> Any:
    start = utcnow()
    t = time.time()
    _write(name, last_started_at=start)
    try:
        out = fn()
    except Exception as e:
        _write(name, last_finished_at=utcnow(), last_error_at=utcnow(), last_error=type(e).__name__[:120],
               last_duration_ms=int((time.time() - t) * 1000), runs=1, failures=1)
        raise
    _write(name, last_finished_at=utcnow(), last_ok_at=utcnow(), last_duration_ms=int((time.time() - t) * 1000),
           last_summary=_summary(out), runs=1)
    return out


def status(db) -> list[dict]:
    rows = {r.name: r for r in db.scalars(select(BackgroundJobStatus))}
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    out = []
    for name, label in JOBS.items():
        r = rows.get(name)
        ok = None if r is None or r.last_finished_at is None else (
            r.last_error_at is None or (r.last_ok_at is not None and r.last_ok_at >= r.last_error_at))
        out.append({"name": name, "label": label, "ok": ok, "last_ok_at": iso(r.last_ok_at) if r else None,
                    "last_error_at": iso(r.last_error_at) if r else None, "last_error": r.last_error if r else None,
                    "last_finished_at": iso(r.last_finished_at) if r else None,
                    "last_duration_ms": r.last_duration_ms if r else None, "runs": r.runs if r else 0,
                    "failures": r.failures if r else 0, "last_summary": r.last_summary if r else None})
    return out
