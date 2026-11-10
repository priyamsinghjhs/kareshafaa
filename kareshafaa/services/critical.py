"""Critical-result fast lane (screening-to-care principle 5: "critical results use a fast lane, not a queue").

A critical (T0) result must not wait in a worklist:

1. The responsible clinician is contacted DIRECTLY (phone, in person or a paged secure message) and READS THE RESULT
   BACK. The clinician records that contact here: when, how, who gave the result, who received it, read-back
   confirmed. Until it is recorded the case sits in the "Critical: read-back needed" worklist and cannot be triaged
   (workflow._guard), so no critical case can move on without a named clinician having it.
2. The first breach of a step clock on a critical case is escalated by PHONE (services/escalation.py sets
   Escalation.channel = "phone"), not by an internal message.
3. A critical case can never be closed as "unable to contact" (statemachine.check_closure): it keeps escalating
   until a clinician decides.

Which results are critical, and the T0 clocks, come from the clinical rules and the SLA table (both placeholders
until clinical governance approves them - docs/clinical_rules.md). Nothing here adds a new clinical timeframe.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Case, CriticalReadBack, Practitioner, utcnow
from ..security import Actor
from ..statemachine import S
from .errors import Forbidden, Invalid

CRITICAL_TIER = "critical"
METHODS: dict[str, str] = {"phone": "Phone call", "in_person": "In person", "secure_page": "Paged secure message"}
PERMISSION = "critical:readback"


def is_critical(case: Case) -> bool:
    return case.tier == CRITICAL_TIER


def latest(db: Session, case_id: int) -> CriticalReadBack | None:
    return db.scalar(select(CriticalReadBack).where(CriticalReadBack.case_id == case_id,
                                                    CriticalReadBack.read_back_confirmed.is_(True))
                     .order_by(CriticalReadBack.id.desc()).limit(1))


def read_back_needed(db: Session, case: Case) -> bool:
    """True for an open critical case with no confirmed read-back on record."""
    return is_critical(case) and S(case.state) != S.CLOSED and latest(db, case.id) is None


def missing_select():
    """SQL: ids of cases that HAVE a confirmed read-back (for the worklist filter)."""
    return select(CriticalReadBack.case_id).where(CriticalReadBack.read_back_confirmed.is_(True))


def record_read_back(db: Session, case: Case, actor: Actor, *, method: str, reported_by: str,
                     read_back_confirmed: bool, received_by_practitioner_id: int | None = None,
                     contacted_at: datetime | None = None, now: datetime | None = None) -> CriticalReadBack:
    """A clinician records the direct contact and read-back for a critical result (audited, same transaction)."""
    from .workflow import _claim

    now = now or utcnow()
    if not actor.is_human or not actor.can(PERMISSION):
        raise Forbidden("only a clinician may record a critical-result read-back")
    if not is_critical(case):
        raise Invalid("the read-back step is for critical (T0) results")
    if S(case.state) == S.CLOSED:
        raise Invalid("the case is closed")
    if method not in METHODS:
        raise Invalid(f"method must be one of {sorted(METHODS)}")
    reported_by = (reported_by or "").strip()
    if len(reported_by) < 2:
        raise Invalid("say who gave you the result (name or role, e.g. 'lab duty scientist')")
    if not read_back_confirmed:
        raise Invalid("read the result back to the person who gave it and tick 'read back and confirmed'. If you "
                      "could not be reached directly, the escalation goes to the on-call route by phone")
    rid = received_by_practitioner_id or actor.practitioner_id
    rec = db.get(Practitioner, rid) if rid else None
    if rec is None or not rec.active or rec.is_pool:
        raise Invalid("the clinician who received the result must be an active, named practitioner")
    at = contacted_at or now
    if at > now + timedelta(minutes=5):
        raise Invalid("the contact time cannot be in the future")
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    row = CriticalReadBack(case_id=case.id, contacted_at=at, method=method, reported_by=reported_by[:120],
                           received_by_practitioner_id=rec.id, read_back_confirmed=True,
                           recorded_by_user_id=actor.user_id, recorded_at=now)
    db.add(row)
    db.flush()
    # pseudonymous: ids and the method only (no patient details, no free text)
    audit.record(db, actor, "C", "critical.read_back_recorded", entity_type="CriticalReadBack", entity_id=row.id,
                 case_id=case.id, ts=now, detail={"method": method, "received_by_practitioner_id": rec.id,
                                                  "contacted_at": at.isoformat()})
    return row


def status(db: Session, case: Case) -> dict | None:
    """Fast-lane facts for the case page (None for non-critical cases)."""
    if not is_critical(case):
        return None
    rb = latest(db, case.id)
    out = {"critical": True, "read_back_needed": rb is None and S(case.state) != S.CLOSED,
           "methods": METHODS, "read_back": None}
    if rb is not None:
        who = db.get(Practitioner, rb.received_by_practitioner_id)
        out["read_back"] = {"contacted_at": rb.contacted_at.isoformat(), "method": rb.method,
                            "method_label": METHODS.get(rb.method, rb.method), "reported_by": rb.reported_by,
                            "received_by": who.name if who else None, "recorded_at": rb.recorded_at.isoformat()}
    return out
