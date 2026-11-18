"""Evaluation (judge) sign-in accounts: one per role, password ONLY from KARESHAFAA_JUDGE_PASSWORD.

Rules (tested in tests/test_judge_accounts.py):
* the password comes only from the environment variable KARESHAFAA_JUDGE_PASSWORD (Render dashboard). It is never in
  the repository, never logged and never shown. It is hashed with the same argon2id hasher as every other password.
* variable set (and it passes the password policy): every judge account is created or updated with that password on
  each start. Variable unset, empty or too weak: judge accounts have NO usable password (cannot sign in) and their
  open sessions are revoked. Changing the value rotates the password (and revokes open sessions).
* staff judge accounts have NO e-mail address, so queue-owner e-mails are skipped for them.
* clinical judge accounts get their OWN staff directory record, a copy of the matching existing account's record
  (same role, organisation, programme, specialty and supervisor). Work queues are decided by role and organisation
  (services/queue.py), so each judge sees the same queue and has the same permissions as that account, without
  sharing its directory record (one account per record, as admin_users requires) or receiving its e-mails.
* judge patient accounts are linked to an EXISTING synthetic sample patient (MRN SYN-..., reserved example.com
  placeholder or no e-mail, so no mail is ever delivered) in that language that has an open case with a result,
  preferably a booked appointment and a recorded understanding check, and that no other account uses. The link is kept
  once made, so judges always return to the same record. No new clinical data is fabricated on the live database.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .models import Appointment, Case, Patient, Practitioner, QuestionnaireResponse, User, utcnow

log = logging.getLogger("kareshafaa.accounts")

ENV_VAR = "KARESHAFAA_JUDGE_PASSWORD"
JUDGE_PREFIX = "judge."


@dataclass(frozen=True)
class JudgeSpec:
    username: str
    role: str
    display_name: str
    like: str | None = None       # existing account whose role, directory record and scope are copied
    language: str | None = None   # patient accounts: language of the linked sample patient


JUDGE_ACCOUNTS: tuple[JudgeSpec, ...] = (
    JudgeSpec("judge.coordinator", "coordinator", "Evaluation Reviewer (Care Coordinator)", like="aisha.rahman"),
    JudgeSpec("judge.gp", "gp", "Evaluation Reviewer (GP)", like="omar.haddad"),
    JudgeSpec("judge.specialist", "specialist", "Evaluation Reviewer (Specialist)", like="vikram.mehta"),
    JudgeSpec("judge.clinicallead", "clinical_lead", "Evaluation Reviewer (Clinical Lead)", like="fatima.al.mansoori"),
    JudgeSpec("judge.analyst", "analyst", "Evaluation Reviewer (Programme Analytics)", like="analyst"),
    JudgeSpec("judge.admin", "admin", "Evaluation Reviewer (System Administrator)", like="admin"),
    JudgeSpec("judge.patient.en", "patient", "Evaluation patient (en)", language="en"),
    JudgeSpec("judge.patient.ar", "patient", "Evaluation patient (ar)", language="ar"),
)
JUDGE_USERNAMES: tuple[str, ...] = tuple(j.username for j in JUDGE_ACCOUNTS)


def is_judge_username(username: str | None) -> bool:
    return (username or "").strip().lower() in JUDGE_USERNAMES


def judge_password() -> str | None:
    """The configured password, or None when unset/blank. Never log or return it anywhere else."""
    v = os.environ.get(ENV_VAR)
    return v if v and v.strip() else None


# ------------------------------------------------------------------------------------------- staff directory record
def _source_practitioner(db: Session, spec: JudgeSpec) -> Practitioner | None:
    src = db.scalar(select(User).where(User.username == spec.like)) if spec.like else None
    if src is not None and src.practitioner_id:
        p = db.get(Practitioner, src.practitioner_id)
        if p is not None and p.role == spec.role and not p.is_pool:
            return p
    judge_ids = select(User.practitioner_id).where(User.username.in_(JUDGE_USERNAMES),
                                                   User.practitioner_id.is_not(None))
    return db.scalar(select(Practitioner).where(Practitioner.role == spec.role, Practitioner.is_pool.is_(False),
                                                Practitioner.active.is_(True), Practitioner.id.not_in(judge_ids))
                     .order_by(Practitioner.id).limit(1))


def _ensure_practitioner(db: Session, spec: JudgeSpec, u: User | None) -> Practitioner | None:
    from .accounts import CLINICAL_ROLES  # noqa: PLC0415

    if spec.role not in CLINICAL_ROLES:
        return None
    src = _source_practitioner(db, spec)
    own = db.get(Practitioner, u.practitioner_id) if u is not None and u.practitioner_id else None
    if own is not None and src is not None and own.id == src.id:
        own = None  # never share the copied account's record
    if src is None and own is None:
        return None
    if own is None:
        own = Practitioner(name=spec.display_name, role=spec.role, org_id=src.org_id)  # type: ignore[union-attr]
        db.add(own)
    own.name, own.role, own.is_pool, own.active = spec.display_name, spec.role, False, True
    if src is not None:  # keep the same scope as the matching account
        own.org_id, own.programme, own.specialty, own.supervisor_id = (src.org_id, src.programme, src.specialty,
                                                                        src.supervisor_id)
    db.flush()
    return own


# ------------------------------------------------------------------------------------------- patient record
def _patient_ok(p: Patient | None) -> bool:
    from .comms import mail  # noqa: PLC0415

    if p is None or p.deceased or not (p.mrn or "").upper().startswith("SYN-"):
        return False
    e = (p.email or "").strip()
    return not e or mail.is_synthetic_address(e)  # never a record that could e-mail a real or test inbox


def pick_patient(db: Session, language: str, exclude_ids: set[int] | None = None) -> Patient | None:
    """Best unused synthetic sample patient in this language (deterministic: lowest id within the best tier)."""
    now = utcnow()
    linked = select(User.patient_id).where(User.patient_id.is_not(None))
    open_case = (Case.patient_id == Patient.id) & (Case.state != "closed")
    booked = exists().where(Appointment.case_id == Case.id, Appointment.status == "booked")
    booked_future = exists().where(Appointment.case_id == Case.id, Appointment.status == "booked",
                                   Appointment.start >= now)
    teach_back = exists().where(QuestionnaireResponse.case_id == Case.id)
    tiers = ((booked_future, teach_back), (booked, teach_back), (booked,), (teach_back,), ())
    for extra in tiers:
        q = (select(Patient).where(Patient.mrn.like("SYN-%"), Patient.language == language,
                                   Patient.deceased.is_not(True), Patient.id.not_in(linked),
                                   exists().where(open_case, *extra))
             .order_by(Patient.id).limit(25))
        for p in db.scalars(q):
            if (not exclude_ids or p.id not in exclude_ids) and _patient_ok(p):
                return p
    return None


# ------------------------------------------------------------------------------------------- provisioning
def provision_judge_accounts(db: Session) -> dict:
    """Create/update (variable set) or disable (unset/blank/weak) the judge accounts. Idempotent; commits.
    Returns a summary that never contains a password."""
    from .accounts import ROLE_LABELS, password_problems, revoke_user_sessions, set_password, verify_password  # noqa: PLC0415

    pw = judge_password()
    usable = pw is not None and not password_problems(pw)
    users = {u.username: u for u in db.scalars(select(User).where(User.username.in_(JUDGE_USERNAMES)))}
    if not usable:
        disabled = 0
        for u in users.values():
            if u.password_hash:
                u.password_hash = None
                revoke_user_sessions(db, u.id)
                disabled += 1
            if u.role != "patient":
                u.email = None
        db.commit()
        if pw is not None:
            log.warning("JUDGE ACCOUNTS NOT USABLE: %s is set but too weak (12+ characters, not a common password). "
                        "Nobody can sign in to the evaluation accounts until it is changed and the service restarts.",
                        ENV_VAR)
        return {"mode": "weak" if pw is not None else "unset", "accounts": len(users), "disabled": disabled}

    created = changed = 0
    missing: list[str] = []
    taken: set[int] = set()
    for spec in JUDGE_ACCOUNTS:
        u = users.get(spec.username)
        prac = None
        patient_id = None
        if spec.role == "patient":
            cur = db.get(Patient, u.patient_id) if u is not None and u.patient_id else None
            if not _patient_ok(cur):
                cur = pick_patient(db, spec.language or "en", taken)
            if cur is None:
                missing.append(spec.username)
                continue
            patient_id = cur.id
            taken.add(cur.id)
        else:
            prac = _ensure_practitioner(db, spec, u)
            from .accounts import CLINICAL_ROLES  # noqa: PLC0415
            if spec.role in CLINICAL_ROLES and prac is None:
                missing.append(spec.username)
                continue
        if u is None:
            u = User(username=spec.username, display_name=spec.display_name, role=spec.role)
            db.add(u)
            created += 1
        u.display_name, u.role, u.active = spec.display_name, spec.role, True
        u.is_demo_account = False
        u.practitioner_id = prac.id if prac is not None else None
        u.patient_id = patient_id
        u.team = ROLE_LABELS.get(spec.role)
        u.site = prac.org.name if prac is not None and prac.org is not None else None
        if spec.role != "patient":
            u.email = None  # no e-mail: queue-owner and staff mail are skipped
        if not verify_password(u.password_hash, pw) or u.must_change_password:  # type: ignore[arg-type]
            if u.id is not None and u.password_hash:
                revoke_user_sessions(db, u.id)  # rotated: sign everyone out of the old password
            set_password(u, pw, must_change=False)  # type: ignore[arg-type]
            changed += 1
        db.flush()
    db.commit()
    if missing:
        log.warning("judge accounts not created (no matching directory record or sample patient): %s",
                    ", ".join(missing))
    return {"mode": "env", "accounts": len(JUDGE_ACCOUNTS) - len(missing), "created": created,
            "password_set": changed, "missing": missing}
