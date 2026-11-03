"""Data retention settings and the automatic purge of short-lived operational data.

Clinical data and the audit trail are never deleted by the application: UAE Federal Law No. 2 of 2019 (use of ICT in
health fields), Article 20, requires health data to be kept for at least 25 years from the last health procedure.
Only operational security data with no clinical content is purged automatically."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from .. import audit
from ..models import LoginAttempt, UserSession, utcnow
from ..security import SYSTEM

CLINICAL_RETENTION_YEARS = 25      # CONFIRM: legal minimum, counted from the last health procedure (not enforced by deletion)
AUDIT_RETENTION_YEARS = 25         # CONFIRM: audit trail kept with the record it describes
LOGIN_ATTEMPT_RETENTION_DAYS = 90  # CONFIRM: failed / successful sign-in attempts (lockout counters) are purged after 90 days
SESSION_RETENTION_DAYS = 30        # CONFIRM: ended or expired sign-in sessions are purged 30 days after they ended
PURGE_EVERY_MINUTES = 60           # the purge runs with the background deadline check, at most once an hour

SETTINGS = [
    {"data": "Patient records, results, cases, care plans, appointments, messages", "keep": f"at least {CLINICAL_RETENTION_YEARS} years after the last health procedure",
     "how": "never deleted by the application; deactivation instead of deletion", "basis": "UAE Federal Law No. 2 of 2019, Art. 20", "automatic": False},
    {"data": "Audit trail (every view and change, hash-chained)", "keep": f"{AUDIT_RETENTION_YEARS} years, with the record it describes",
     "how": "append-only; rows cannot be changed or deleted (database guard)", "basis": "UAE Federal Law No. 2 of 2019, Art. 4 and 20; ADHICS V2", "automatic": False},
    {"data": "Staff and patient sign-in accounts", "keep": "for as long as the record they belong to", "how": "deactivated, never hard-deleted, so the audit trail stays readable",
     "basis": "ADHICS V2 (access control)", "automatic": False},
    {"data": "Sign-in attempts (lockout counters)", "keep": f"{LOGIN_ATTEMPT_RETENTION_DAYS} days", "how": "purged automatically",
     "basis": "minimum necessary (security operations only)", "automatic": True},
    {"data": "Ended or expired sign-in sessions", "keep": f"{SESSION_RETENTION_DAYS} days after they end", "how": "purged automatically (only a hash of the token is stored)",
     "basis": "minimum necessary", "automatic": True},
]

_LAST: dict[str, datetime] = {}


def purge_operational(db: Session, now: datetime | None = None, force: bool = False) -> dict:
    now = now or utcnow()
    last = _LAST.get("purge")
    if not force and last is not None and now - last < timedelta(minutes=PURGE_EVERY_MINUTES):
        return {"skipped": "ran recently"}
    _LAST["purge"] = now
    la = db.execute(delete(LoginAttempt).where(LoginAttempt.at < now - timedelta(days=LOGIN_ATTEMPT_RETENTION_DAYS))).rowcount or 0
    cut = now - timedelta(days=SESSION_RETENTION_DAYS)
    us = db.execute(delete(UserSession).where(or_(UserSession.expires_at < cut, UserSession.revoked_at < cut))).rowcount or 0
    if la or us:
        audit.record(db, SYSTEM, "D", "retention.purged", ts=now,
                     detail={"login_attempts": la, "sessions": us, "login_attempt_days": LOGIN_ATTEMPT_RETENTION_DAYS,
                             "session_days": SESSION_RETENTION_DAYS})
    db.commit()
    return {"login_attempts": la, "sessions": us}
