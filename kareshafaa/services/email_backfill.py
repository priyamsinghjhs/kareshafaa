"""Idempotent placeholder e-mail backfill, and live assignment of allowlisted role test inboxes.

Uses the reserved RFC 2606 domain example.com (never a real mailbox) for empty Patient and User addresses.
Existing addresses, including the allowlisted test inboxes in synth/test_mailboxes.py, are never overwritten
by the placeholder step. apply_test_mailboxes assigns those mapped inboxes on a live database (including
replacing a placeholder or another address already on that account). comms/mail.py already refuses to send
to example.com / .org / .net. A failure here must not stop the server (bootstrap warns and continues).
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


def _norm(addr: str | None) -> str:
    return (addr or "").strip().lower()


def _inbox_taken(db: Session, addr: str, *, exclude_user_id: int | None = None,
                 exclude_patient_id: int | None = None) -> bool:
    """True when another User or Patient row already holds this address (case-insensitive)."""
    addr = _norm(addr)
    if not addr:
        return False
    q = select(User.id).where(func.lower(func.trim(User.email)) == addr)
    if exclude_user_id is not None:
        q = q.where(User.id != exclude_user_id)
    if db.scalar(q.limit(1)) is not None:
        return True
    q = select(Patient.id).where(func.lower(func.trim(Patient.email)) == addr)
    if exclude_patient_id is not None:
        q = q.where(Patient.id != exclude_patient_id)
    return db.scalar(q.limit(1)) is not None


def apply_test_mailboxes(db: Session, now: datetime | None = None) -> dict:
    """Assign the allowlisted role test inboxes on a live database (idempotent).

    For each username in synth/test_mailboxes.py, if the User exists, set User.email to the mapped
    inbox — even when a different address is already stored (placeholders and other values). For
    patient-role mappings, also set the linked Patient.email the same way. Never overwrites a
    different row that already holds the inbox (skipped with a warning). A missing username is
    skipped with a warning. Audits each change with the previous address masked.
    """
    from ..comms import mail
    from .invites import mask_email
    from ..synth.test_mailboxes import PATIENT_MAILBOXES, STAFF_MAILBOXES

    now = now or utcnow()
    n_user = n_pat = n_over = n_already = n_conflict = n_missing = 0
    changes: list[dict] = []

    def _assign(row, *, field: str, username: str, target: str, exclude_user_id: int | None,
                exclude_patient_id: int | None) -> str:
        nonlocal n_over, n_already, n_conflict
        target_l = _norm(target)
        current = (getattr(row, "email", None) or "").strip()
        if _norm(current) == target_l:
            n_already += 1
            return "already"
        if _inbox_taken(db, target, exclude_user_id=exclude_user_id, exclude_patient_id=exclude_patient_id):
            n_conflict += 1
            log.warning("test inbox for %s not applied to %s: already held by another record", username, field)
            return "conflict"
        if current:
            n_over += 1
        row.email = target
        changes.append({
            "username": username,
            "field": field,
            "old_masked": mask_email(current) if current else None,
            "was_placeholder": mail.is_synthetic_address(current) if current else False,
        })
        return "set"

    mapping: list[tuple[str, str, bool]] = (
        [(u, a, False) for u, a in STAFF_MAILBOXES.items()]
        + [(u, a, True) for u, a in PATIENT_MAILBOXES.items()]
    )
    for username, inbox, is_patient in mapping:
        target = (inbox or "").strip()
        if not target:
            continue
        u = db.scalar(select(User).where(func.lower(User.username) == username))
        if u is None:
            n_missing += 1
            log.warning("test inbox for %s not applied: user not found", username)
            continue
        linked_pid = u.patient_id if is_patient else None
        status = _assign(u, field="user", username=username, target=target,
                         exclude_user_id=u.id, exclude_patient_id=linked_pid)
        if status == "set":
            n_user += 1
        if not is_patient:
            continue
        p = db.get(Patient, linked_pid) if linked_pid else None
        if p is None:
            log.warning("test inbox for %s not applied to patient: no linked patient record", username)
            continue
        status = _assign(p, field="patient", username=username, target=target,
                         exclude_user_id=u.id, exclude_patient_id=p.id)
        if status == "set":
            n_pat += 1

    db.flush()
    out = {"users": n_user, "patients": n_pat, "overwrites": n_over, "already": n_already,
           "conflicts": n_conflict, "missing": n_missing}
    if n_user or n_pat:
        audit.record(db, SYSTEM, "U", "directory.test_inboxes_applied", entity_type="Directory",
                     entity_id="email", ts=now, detail={**out, "changes": changes})
        log.info("applied test inboxes to %s user and %s patient record(s) "
                 "(%s overwritten, %s already set, %s conflict, %s missing)",
                 n_user, n_pat, n_over, n_already, n_conflict, n_missing)
    return out
