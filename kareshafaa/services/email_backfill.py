"""Idempotent placeholder e-mail backfill for any Patient or User still missing an address.

Uses the reserved RFC 2606 domain example.com (never a real mailbox). Existing addresses, including the
allowlisted test inboxes in synth/test_mailboxes.py, are never overwritten. comms/mail.py already refuses
to send to example.com / .org / .net. A failure here must not stop the server (bootstrap warns and continues).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Patient, User, utcnow
from ..security import SYSTEM

log = logging.getLogger("kareshafaa.mail")

PLACEHOLDER_DOMAIN = "example.com"
_TOKEN = re.compile(r"[^a-z0-9]+")


def _token(raw: str | None, default: str) -> str:
    t = _TOKEN.sub("", (raw or "").lower())
    return (t[:40] or default)


def placeholder_address(*, rec_id: int, given: str | None = None, family: str | None = None,
                        username: str | None = None) -> str:
    """firstname.lastname.<id>@example.com (patients / named staff) or username.<id>@example.com."""
    if (given or "").strip() or (family or "").strip():
        local = f"{_token(given, 'given')}.{_token(family, 'family')}.{int(rec_id)}"
    else:
        local = f"{_token(username, 'user')}.{int(rec_id)}"
    return f"{local}@{PLACEHOLDER_DOMAIN}"


def _empty(value: str | None) -> bool:
    return not (value or "").strip()


def _taken(db: Session) -> set[str]:
    out: set[str] = set()
    for model in (Patient, User):
        for (addr,) in db.execute(select(model.email).where(model.email.is_not(None))):
            if not _empty(addr):
                out.add(addr.strip().lower())
    return out


def _unique(candidate: str, taken: set[str]) -> str:
    addr, n = candidate, 2
    local, _, domain = candidate.partition("@")
    while addr.lower() in taken:
        addr = f"{local}.{n}@{domain}"
        n += 1
    taken.add(addr.lower())
    return addr


def address_for_patient(p: Patient, taken: set[str]) -> str:
    return _unique(placeholder_address(rec_id=p.id, given=p.given_name, family=p.family_name), taken)


def address_for_user(u: User, taken: set[str]) -> str:
    given = family = None
    display = (u.display_name or "").strip()
    # "Dr. Aisha Rahman (Care coordinator)" -> Aisha Rahman
    if display:
        name = display.split(" (")[0].replace("Dr. ", "").replace("Dr ", "").strip()
        parts = name.split()
        if len(parts) >= 2:
            given, family = parts[0], parts[-1]
        elif parts:
            given = parts[0]
    return _unique(placeholder_address(rec_id=u.id, given=given, family=family, username=u.username), taken)


def backfill_missing_emails(db: Session, now: datetime | None = None) -> dict:
    """Fill every empty Patient.email and User.email with a unique example.com placeholder. Idempotent."""
    now = now or utcnow()
    taken = _taken(db)
    n_pat = n_user = 0
    for p in db.scalars(select(Patient).where(or_(Patient.email.is_(None), func.trim(Patient.email) == "")).order_by(Patient.id)):
        if not _empty(p.email):
            continue
        p.email = address_for_patient(p, taken)
        n_pat += 1
    for u in db.scalars(select(User).where(or_(User.email.is_(None), func.trim(User.email) == "")).order_by(User.id)):
        if not _empty(u.email):
            continue
        u.email = address_for_user(u, taken)
        n_user += 1
    db.flush()
    if n_pat or n_user:
        audit.record(db, SYSTEM, "U", "directory.email_placeholders_backfilled", entity_type="Directory",
                     entity_id="email", ts=now,
                     detail={"patients": n_pat, "users": n_user, "overwrites": 0, "domain": PLACEHOLDER_DOMAIN})
        log.info("filled %s empty patient and %s empty user e-mail(s) with @%s placeholders",
                 n_pat, n_user, PLACEHOLDER_DOMAIN)
    return {"patients": n_pat, "users": n_user, "overwrites": 0, "domain": PLACEHOLDER_DOMAIN}
