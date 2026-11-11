"""Stored SLA thresholds for the work queue (derived data, kept in step with every change).

Each OPEN case carries two absolute timestamps taken from its in-progress step:

* ``sla_amber_at`` – the first moment the step's RAG status is no longer green (reminder point),
* ``sla_red_at``   – the first moment it is red (step due / overdue).

Both are NULL when the case has no running clock (RAG "none") or is closed. RAG is NOT stored: the queue compares
the thresholds with the current time in SQL (red if now >= sla_red_at, amber if now >= sla_amber_at, else green),
so time-based green → amber → red transitions are always current. The thresholds are computed with the same
``sla.rag_status`` function the queue used before (exact to the microsecond), from the step's start and due time,
the case tier and the active SLA rule's reminder fraction.

Kept correct by an ORM ``after_flush`` hook: any flushed change to a Case (state, tier, closure, reopen, escalation,
...) or a StepTask (step started, completed, re-timed, paused) recomputes that case; a change to an SLA rule
recomputes every open case. ``backfill`` recomputes everything idempotently at start-up (``init_db``), which also
heals rows written without the ORM (bulk loads). Works the same on SQLite and PostgreSQL (plain comparisons only).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, bindparam, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .models import Case, SlaRule, StepTask
from .sla import rag_reminder_fraction, rag_status

ALWAYS = datetime(1900, 1, 1)
last_stale: list[dict] = []  # details of the last write=False check (diagnostics in tests)  # threshold for a step whose due time is not after its start (always red)
_US = timedelta(microseconds=1)


def _first(pred, guess: datetime, lo: datetime) -> datetime:
    """Smallest microsecond t >= lo with pred(t) True, given a monotone pred and a close guess."""
    t = max(guess, lo)
    for _ in range(64):
        if pred(t):
            break
        t += _US
    else:  # pragma: no cover - only if the guess was far off
        return guess
    for _ in range(64):
        if t > lo and pred(t - _US):
            t -= _US
        else:
            break
    return t


def thresholds(started: datetime | None, due: datetime | None, rf: float) -> tuple[datetime | None, datetime | None]:
    """(amber_at, red_at) so that rag_status(started, due, now, rf) == red iff now >= red_at, amber iff
    amber_at <= now < red_at, green otherwise; (None, None) = "none"."""
    if started is None or due is None:
        return None, None
    total = (due - started).total_seconds()
    if total <= 0:
        return ALWAYS, ALWAYS
    red = _first(lambda t: rag_status(started, due, t, rf)[0] == "red", due, datetime.min + _US)
    if rf >= 1:
        return red, red
    guess = started + timedelta(seconds=rf * total)
    amber = _first(lambda t: rag_status(started, due, t, rf)[0] != "green", guess, datetime.min + _US)
    return min(amber, red), red


def _rules(conn: Connection) -> dict[tuple[str, str], float]:
    # same query and "last row wins" semantics as sla.SlaLookup.load
    t = SlaRule.__table__
    return {(r.state, r.tier): r.reminder_fraction for r in conn.execute(select(t).where(t.c.active.is_(True)))}


def _default_rf() -> float:
    from .config import get_settings

    return get_settings().reminder_fraction


def refresh(conn: Connection, case_ids=None, *, write: bool = True) -> int:
    """Recompute stored thresholds for the given cases (None = all cases). Writes only rows that changed.
    write=False only counts stale rows (checks)."""
    rules = _rules(conn)
    rf_default = _default_rf()
    c, s = Case.__table__, StepTask.__table__
    q = (select(c.c.id, c.c.state, c.c.tier, c.c.sla_amber_at, c.c.sla_red_at, s.c.started_at, s.c.due_at, s.c.state)
         .select_from(c.outerjoin(s, and_(s.c.case_id == c.c.id, s.c.status == "in-progress"))))
    ids = None if case_ids is None else sorted({i for i in case_ids if i is not None})
    chunks = [None] if ids is None else [ids[i:i + 500] for i in range(0, len(ids), 500)]
    changes = []
    for chunk in chunks:
        if chunk is not None and not chunk:
            continue
        qq = q if chunk is None else q.where(c.c.id.in_(chunk))
        for cid, state, tier, old_a, old_r, st_start, st_due, st_state in conn.execute(qq):
            if state == "closed":
                new = (None, None)
            else:
                new = thresholds(st_start, st_due, rag_reminder_fraction(st_state, rules.get((st_state, tier), rf_default)))
            if (old_a, old_r) != new:
                changes.append({"cid": cid, "a": new[0], "r": new[1], "old": (old_a, old_r), "state": state,
                                "tier": tier, "step": (st_start, st_due, st_state)})
    if not write:
        global last_stale
        last_stale = changes
        return len(changes)
    for i in range(0, len(changes), 1000):
        conn.execute(update(c).where(c.c.id == bindparam("cid")).values(sla_amber_at=bindparam("a"),
                                                                         sla_red_at=bindparam("r")),
                     [{"cid": x["cid"], "a": x["a"], "r": x["r"]} for x in changes[i:i + 1000]])
    return len(changes)


def backfill(engine) -> int:
    """Idempotent: recompute every case (start-up / migration)."""
    with engine.begin() as conn:
        return refresh(conn, None)


def _after_flush(session: Session, _ctx) -> None:
    ids: set[int] = set()
    rules_changed = False
    for o in (*session.new, *session.dirty, *session.deleted):
        if isinstance(o, Case):
            ids.add(o.id)
        elif isinstance(o, StepTask):
            ids.add(o.case_id)
        elif isinstance(o, SlaRule):
            rules_changed = True
    if rules_changed:
        refresh(session.connection(), None)
    elif ids:
        refresh(session.connection(), ids)


_registered = False


def register() -> None:
    global _registered
    if not _registered:
        from sqlalchemy import event

        event.listen(Session, "after_flush", _after_flush)
        _registered = True


register()
