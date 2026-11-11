"""E-mail the owner of a work queue when a case is added to it.

Called from the workflow write paths that actually change ownership (new case, assign, accepted
transfer). Never from ordinary state transitions. A mail failure is logged (no secrets) and never
raised: the clinical action has already happened.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Case, Patient, Practitioner, User

log = logging.getLogger("kareshafaa.mail")


def notify_queue_owner(db: Session, case: Case, *, reason: str) -> bool:
    """Send a short staff e-mail to the case owner's User.email. Returns True if a message was sent.
    Never raises."""
    from ..config import get_settings  # noqa: PLC0415
    from ..programmes import PROGRAMMES  # noqa: PLC0415
    from ..statemachine import LABELS, S  # noqa: PLC0415
    from . import mail  # noqa: PLC0415

    try:
        if not mail.configured() or case is None or not case.owner_id:
            return False
        patient = case.patient if getattr(case, "patient", None) is not None else db.get(Patient, case.patient_id)
        if patient is None or _skip_sample_caseload(patient):
            return False
        owner = db.get(Practitioner, case.owner_id)
        if owner is None or owner.is_pool or not owner.active:
            return False
        user = db.scalar(select(User).where(User.practitioner_id == owner.id, User.active.is_(True)))
        if user is None or not (user.email or "").strip():
            return False
        try:
            to = mail.validate_address(user.email)
        except Exception:  # noqa: BLE001 - a bad stored address is not emailed
            return False
        if mail.is_synthetic_address(to):
            log.info("queue-owner e-mail skipped: reserved example domain placeholder (RFC 2606)")
            return False
        prog = PROGRAMMES.get(case.programme)
        prog_name = prog.name if prog else (case.programme or "")
        try:
            state = LABELS.get(S(case.state), case.state)
        except ValueError:
            state = case.state
        patient_name = f"{patient.given_name or ''} {patient.family_name or ''}".strip() or f"patient #{patient.id}"
        why = reason.strip() or "assigned to you"
        lines = [
            "A case was added to your queue.",
            "",
            f"Case: #{case.id}",
            f"Programme: {prog_name}",
            f"State: {state}",
            f"Tier: {case.tier or ''}",
            f"Patient: {patient_name}",
            f"Why: {why}",
        ]
        base = (get_settings().public_base_url or "").rstrip("/")
        if base:
            lines.extend(["", f"Open the case: {base}/cases/{case.id}"])
        mail.send_email(to, "KareShafaa: a case was added to your queue", "\n".join(lines))
        return True
    except mail.MailError as e:
        log.warning("queue-owner e-mail not sent: %s", e)
        return False
    except Exception as e:  # noqa: BLE001 - never fail a clinical write because of mail
        log.warning("queue-owner e-mail skipped: %s", type(e).__name__)
        return False


def _skip_sample_caseload(patient: Patient) -> bool:
    """True for the ~2000 SYN- / example.com sample patients, except the allowlisted test inbox."""
    from ..synth.test_mailboxes import is_allowlisted_patient_address  # noqa: PLC0415
    from . import mail  # noqa: PLC0415

    raw = (getattr(patient, "email", None) or "").strip()
    addr = None
    if raw:
        try:
            addr = mail.validate_address(raw)
        except Exception:  # noqa: BLE001
            addr = None
    if addr and is_allowlisted_patient_address(addr):
        return False
    return mail.is_synthetic_patient(patient)
