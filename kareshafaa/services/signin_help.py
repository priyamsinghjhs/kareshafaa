"""Sign-in help: "Forgot username?" and "Forgot password?" from the sign-in page (added 3 Nov 2026).

* PATIENTS prove who they are with three facts we hold: date of birth, the last 4 characters of their identity
  document (Emirates ID or passport / other ID) and their registered mobile number.
  - Forgot username: on a match the username is shown on the page. Otherwise a generic message.
    SHOW_USERNAME_ON_MATCH is a CONFIRM decision for the clinical lead.
  - Forgot password: if they know their username, or if they confirm the same three facts, a single-use
    reset link (30 minutes) is emailed when the patient-app account exists and the address is deliverable
    (can_send_to_patient / can_send_to_address; test-inbox allowlist applies). A details match also reminds
    them of their username in that email. The page always shows the same generic message (no account
    enumeration). If the identity check matches a patient who has no app password yet, a sign-up /
    activation link is emailed instead. @example.com placeholders and an unconfigured mailbox skip the
    send but still get the generic answer; a mail failure is logged and audited, and the page looks the
    same. The patient sets a new password on /patient-reset-password (existing password policy; every
    session ends; a confirmation email is sent when the address is deliverable). Patients who cannot
    use email may still leave a request for the care team (?via=admin): a coordinator checks who they
    are and sends a one-time RESET code (the activation-code mechanism in services/invites.py). The
    patient then sets a new password on /reset-password with the code and their date of birth; every
    existing session ends.
  Responses never reveal whether an account exists. Repeated failures pause the device, and repeated near-misses
  on one record silently stop that record answering for a while. Every attempt is audited (no typed values).
* STAFF who know their username request a password-reset email: a single-use link (30 minutes) is sent to the
  email on the User record when the account is active and the address is deliverable. The page always shows the
  same generic message (no account enumeration). @example.com placeholders and an unconfigured mailbox skip the
  send but still get the generic answer; a mail failure is logged and audited, and the page looks the same. The
  staff member sets a new password on /staff-reset-password (existing password policy; every session ends; no
  forced change). Staff who cannot use email may still leave a request for the administrator (name, role,
  organisation, contact). Only administrators see it (Admin > Users); they reset the password there (temporary
  password shown once, change forced at next sign-in, all sessions ended).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import accounts, audit
from ..config import get_settings
from ..models import LoginAttempt, Patient, PatientPasswordReset, SigninHelpRequest, StaffPasswordReset, User, utcnow
from ..security import Actor
from ..sla import due_at as sla_due_at
from ..sla import rag_status
from .errors import Forbidden, Invalid, NotFound

log = logging.getLogger("kareshafaa.mail")

# ---- CONFIRM values (security / operations) -----------------------------------------------------------------
SHOW_USERNAME_ON_MATCH = True   # CONFIRM (clinical lead): show a patient's username on screen after a 3-factor match
HELP_WINDOW_MINUTES = 15        # CONFIRM (security): window for counting failed help attempts per device
HELP_IP_MAX_FAILURES = 5        # CONFIRM (security): failed patient checks per device in the window -> paused
HELP_RECORD_MAX_FAILURES = 3    # CONFIRM (security): near-misses on one patient record -> record stops answering
HELP_RECORD_LOCK_HOURS = 24     # CONFIRM (security): ... for this long (silent: the page looks the same)
STAFF_REQUESTS_PER_IP_HOUR = 5  # CONFIRM (operations): staff help requests per device per hour
PATIENT_RESET_RESPONSE_HOURS = 24  # CONFIRM (programme manager): coordinators answer a reset request within (working days)
STAFF_HELP_RESPONSE_HOURS = 24     # CONFIRM (operations): administrators answer a staff request within (working days)

HELP_MARK = "~signin_help"      # LoginAttempt.username marker: failed patient checks (per device)
STAFF_RESET_MARK = "~staff_reset"  # LoginAttempt marker: staff email-reset attempts (per device)
PATIENT_RESET_MARK = "~patient_reset"  # LoginAttempt marker: patient email-reset attempts (per device)
STAFF_RESET_TTL_MINUTES = 30   # CONFIRM (security): a staff email-reset link expires sooner than a patient code
PATIENT_RESET_TTL_MINUTES = 30  # CONFIRM (security): a patient email-reset link expires with the staff link
STAFF_ROLES = ("coordinator", "gp", "specialist", "clinical_lead", "admin", "analyst", "translator", "other")
VERIFY_METHODS = {"in_person_id": "Seen in person with photo ID",
                  "phone_call_checks": "Phone call to the registered mobile: date of birth, ID number and address checked",
                  "video_call_id": "Video call with photo ID"}
SYSTEM = Actor(user_id=None, role="anonymous", display="sign-in help page", is_human=True)


def _record_mark(pid: int) -> str:
    return f"~help:p{pid}"


def _paused(db: Session, ip: str | None, now: datetime) -> bool:
    if not ip:
        return False
    since = now - timedelta(minutes=HELP_WINDOW_MINUTES)
    n = db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.username == HELP_MARK, LoginAttempt.ip == ip,
                                                           LoginAttempt.success.is_(False), LoginAttempt.at >= since)) or 0
    return n >= HELP_IP_MAX_FAILURES


def _record_locked(db: Session, pid: int, now: datetime) -> bool:
    since = now - timedelta(hours=HELP_RECORD_LOCK_HOURS)
    n = db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.username == _record_mark(pid),
                                                           LoginAttempt.success.is_(False), LoginAttempt.at >= since)) or 0
    return n >= HELP_RECORD_MAX_FAILURES


def _last4(raw: str | None) -> str | None:
    s = re.sub(r"[^0-9A-Za-z]", "", raw or "").upper()
    return s if len(s) == 4 else None


def _doc_tail(p: Patient) -> set[str]:
    out = set()
    if p.national_id:
        d = re.sub(r"\D", "", p.national_id)
        if len(d) >= 4:
            out.add(d[-4:])
    if p.id_number:
        d = re.sub(r"[^0-9A-Za-z]", "", p.id_number).upper()
        if len(d) >= 4:
            out.add(d[-4:])
    return out


def _phone(raw: str | None) -> str | None:
    from .patients import normalise_phone

    try:
        return normalise_phone(raw)
    except Invalid:
        return None


def _identify_patient(db: Session, birth_date: date | None, id_last4: str | None, phone: str | None,
                      now: datetime) -> tuple[str, Patient | None, User | None, list[Patient]]:
    """3-factor match. reason is ok, no_match, ambiguous, record_locked, no_active_account or inactive.
    Does not record attempts or audit; the caller does that."""
    tail, mob = _last4(id_last4), _phone(phone)
    cands = list(db.scalars(select(Patient).where(Patient.birth_date == birth_date, Patient.deceased.is_(False)))) \
        if birth_date else []
    full, near = [], []
    for p in cands:
        f_id = tail is not None and tail in _doc_tail(p)
        f_ph = mob is not None and p.phone == mob
        if f_id and f_ph:
            full.append(p)
        elif f_id or f_ph:
            near.append(p)
    if len(full) == 1:
        p = full[0]
        if _record_locked(db, p.id, now):
            return "record_locked", p, None, near
        u = db.scalar(select(User).where(User.patient_id == p.id, User.role == "patient").order_by(User.id).limit(1))
        if u is None or not u.password_hash:
            return "no_active_account", p, u, near
        if not u.active:
            return "inactive", p, u, near
        return "ok", p, u, near
    if len(full) > 1:
        return "ambiguous", None, None, near
    return "no_match", None, None, near


def check_patient(db: Session, kind: str, birth_date: date | None, id_last4: str | None, phone: str | None,
                  ip: str | None, lang: str = "en", now: datetime | None = None) -> tuple[str, User | None]:
    """Returns ("ok", user) on a full 3-factor match with an active patient account, ("paused", None) when this
    device made too many failed attempts, else ("no_match", None). Every attempt is audited; typed values are not."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    event = f"signin_help.patient_{kind}"
    if _paused(db, ip, now):
        audit.record(db, SYSTEM, "E", event, entity_type="Patient", outcome="failure", ts=now,
                     detail={"reason": "rate_limited", "ip": ip, "language": lang})
        return "paused", None
    reason, p, u, near = _identify_patient(db, birth_date, id_last4, phone, now)
    user = u if reason == "ok" else None
    if user is None:
        db.add(LoginAttempt(username=HELP_MARK, ip=ip, at=now, success=False))
        for np in near:  # one fact right, one wrong: count against that record
            db.add(LoginAttempt(username=_record_mark(np.id), ip=ip, at=now, success=False))
    audit.record(db, SYSTEM, "E", event, entity_type="Patient", entity_id=p.id if user is not None else None, ts=now,
                 outcome="success" if user is not None else "failure",
                 detail={"reason": reason, "ip": ip, "language": lang, "near_miss_records": len(near)})
    return ("ok", user) if user is not None else ("no_match", None)


def forgot_username(db: Session, birth_date, id_last4, phone, ip, lang="en", now=None) -> tuple[str, str | None]:
    """("ok", username) | ("no_match", None) | ("paused", None)."""
    st, u = check_patient(db, "username", birth_date, id_last4, phone, ip, lang, now)
    if st == "ok" and SHOW_USERNAME_ON_MATCH:
        return "ok", u.username  # type: ignore[union-attr]
    return ("paused" if st == "paused" else "no_match"), None


def forgot_password(db: Session, birth_date, id_last4, phone, ip, lang="en", now=None) -> str:
    """Care-team fallback (?via=admin). "sent" (the page always says the same thing) | "paused".
    On a match a reset request reaches coordinators."""
    now = now or utcnow()
    st, u = check_patient(db, "password", birth_date, id_last4, phone, ip, lang, now)
    if st == "paused":
        return "paused"
    if st == "ok":
        open_ = db.scalar(select(SigninHelpRequest).where(SigninHelpRequest.audience == "patient",
                                                          SigninHelpRequest.patient_id == u.patient_id,  # type: ignore[union-attr]
                                                          SigninHelpRequest.status == "open"))
        if open_ is None:
            r = SigninHelpRequest(audience="patient", kind="password", patient_id=u.patient_id, user_id=u.id,  # type: ignore[union-attr]
                                  language=lang, ip=(ip or "")[:64] or None, status="open", created_at=now,
                                  due_at=sla_due_at(now, PATIENT_RESET_RESPONSE_HOURS, "high"))
            db.add(r)
            db.flush()
            audit.record(db, SYSTEM, "C", "signin_help.reset_requested", entity_type="Patient", entity_id=u.patient_id,  # type: ignore[union-attr]
                         ts=now, detail={"request_id": r.id, "user_id": u.id, "language": lang})  # type: ignore[union-attr]
        else:
            audit.record(db, SYSTEM, "E", "signin_help.reset_requested_again", entity_type="Patient",
                         entity_id=u.patient_id, ts=now, detail={"request_id": open_.id})  # type: ignore[union-attr]
    return "sent"


def staff_request(db: Session, *, full_name: str, role: str, org: str, contact: str, need: str, note: str | None,
                  ip: str | None, lang: str = "en", now: datetime | None = None) -> tuple[str, list[str]]:
    """("sent", []) | ("invalid", [field, ...]) | ("paused", []). Nothing is matched to an account here."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    bad = [f for f, ok in (("name", len((full_name or "").strip()) >= 2), ("role", role in STAFF_ROLES),
                           ("org", len((org or "").strip()) >= 2), ("contact", len((contact or "").strip()) >= 5)) if not ok]
    if bad:
        return "invalid", bad
    since = now - timedelta(hours=1)
    if ip and (db.scalar(select(func.count(SigninHelpRequest.id)).where(
            SigninHelpRequest.audience == "staff", SigninHelpRequest.ip == ip, SigninHelpRequest.created_at >= since)) or 0) >= STAFF_REQUESTS_PER_IP_HOUR:
        audit.record(db, SYSTEM, "E", "signin_help.staff_request", entity_type="SigninHelpRequest", outcome="failure",
                     ts=now, detail={"reason": "rate_limited", "ip": ip})
        return "paused", []
    r = SigninHelpRequest(audience="staff", kind="username" if need == "username" else "password",
                          full_name=full_name.strip()[:200], role_text=role, org_text=org.strip()[:200],
                          contact=contact.strip()[:200], note=(note or "").strip()[:1000] or None, language=lang, ip=ip,
                          status="open", created_at=now, due_at=sla_due_at(now, STAFF_HELP_RESPONSE_HOURS, "high"))
    db.add(r)
    db.flush()
    audit.record(db, SYSTEM, "C", "signin_help.staff_request", entity_type="SigninHelpRequest", entity_id=r.id, ts=now,
                 detail={"kind": r.kind, "role": role, "ip": ip, "language": lang})
    return "sent", []


# ------------------------------------------------------------------ staff side
def _can(actor: Actor, audience: str) -> None:
    if audience == "patient":
        if not (actor.role == "coordinator" and actor.can("patient:invite")):
            raise Forbidden("patient password resets are handled by care coordinators")
    elif audience == "staff":
        if not actor.can("user:admin"):
            raise Forbidden("staff sign-in help requests are handled by system administrators")
    else:
        raise Invalid("audience must be patient or staff")


def _row(db: Session, r: SigninHelpRequest, now: datetime) -> dict:
    rag, frac = rag_status(r.created_at, r.due_at, now) if r.status == "open" else ("none", 0.0)
    d = {"id": r.id, "audience": r.audience, "kind": r.kind, "status": r.status, "created_at": r.created_at.isoformat(),
         "due_at": r.due_at.isoformat() if r.due_at else None, "rag": rag, "elapsed": round(frac, 2),
         "language": r.language, "handled_at": r.handled_at.isoformat() if r.handled_at else None,
         "handled_by": r.handled_by, "outcome_note": r.outcome_note, "verify_method": r.verify_method,
         "invite_id": r.invite_id}
    if r.audience == "patient":
        p = db.get(Patient, r.patient_id) if r.patient_id else None
        d["patient"] = {"id": p.id, "name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "language": p.language} if p else None
    else:
        d.update({"full_name": r.full_name, "role": r.role_text, "org": r.org_text, "contact": r.contact, "note": r.note})
    return d


def list_requests(db: Session, actor: Actor, audience: str, status: str = "open", now: datetime | None = None) -> dict:
    _can(actor, audience)
    now = now or utcnow()
    q = select(SigninHelpRequest).where(SigninHelpRequest.audience == audience)
    if status != "all":
        q = q.where(SigninHelpRequest.status == status)
    rows = db.scalars(q.order_by(SigninHelpRequest.due_at, SigninHelpRequest.id).limit(200)).all()
    items = [_row(db, r, now) for r in rows]
    return {"items": items, "open": sum(1 for i in items if i["status"] == "open"),
            "overdue": sum(1 for i in items if i["rag"] == "red"), "verify_methods": VERIFY_METHODS}


def open_patient_request(db: Session, patient_id: int) -> SigninHelpRequest | None:
    return db.scalar(select(SigninHelpRequest).where(SigninHelpRequest.audience == "patient",
                                                     SigninHelpRequest.patient_id == patient_id,
                                                     SigninHelpRequest.status == "open").order_by(SigninHelpRequest.id.desc()).limit(1))


def close_request(db: Session, actor: Actor, request_id: int, outcome: str, note: str,
                  now: datetime | None = None) -> SigninHelpRequest:
    now = now or utcnow()
    r = db.get(SigninHelpRequest, request_id)
    if r is None:
        raise NotFound("request not found")
    _can(actor, r.audience)
    if r.status != "open":
        raise Invalid(f"this request is already {r.status}")
    if outcome not in ("done", "closed"):
        raise Invalid("outcome must be done or closed")
    note = (note or "").strip()
    if len(note) < 5:
        raise Invalid("say what was done (at least 5 characters)")
    r.status, r.handled_at, r.handled_by, r.handled_by_user_id = outcome, now, actor.display, actor.user_id
    r.outcome_note = note[:1000]
    audit.record(db, actor, "U", f"signin_help.request_{outcome}", entity_type="SigninHelpRequest", entity_id=r.id,
                 ts=now, detail={"audience": r.audience, "kind": r.kind, "note": r.outcome_note,
                                 "answered_late": bool(r.due_at and now > r.due_at)})
    return r


def reset_code_issued(db: Session, actor: Actor, patient_id: int, invite_id: int, verify_method: str,
                      now: datetime | None = None) -> None:
    """A coordinator sent a reset code: the patient's open reset request is done."""
    now = now or utcnow()
    r = open_patient_request(db, patient_id)
    if r is None:
        return
    r.status, r.handled_at, r.handled_by, r.handled_by_user_id = "done", now, actor.display, actor.user_id
    r.verify_method, r.invite_id = verify_method, invite_id
    r.outcome_note = f"Reset code sent after identity check: {VERIFY_METHODS.get(verify_method, verify_method)}"
    audit.record(db, actor, "U", "signin_help.request_done", entity_type="SigninHelpRequest", entity_id=r.id, ts=now,
                 detail={"audience": "patient", "invite_id": invite_id, "verify_method": verify_method,
                         "answered_late": bool(r.due_at and now > r.due_at)})


def open_counts(db: Session) -> dict:
    rows = db.execute(select(SigninHelpRequest.audience, func.count(SigninHelpRequest.id))
                      .where(SigninHelpRequest.status == "open").group_by(SigninHelpRequest.audience)).all()
    return {a: n for a, n in rows}


# ------------------------------------------------------------------ staff email reset
def _staff_reset_user_mark(uid: int) -> str:
    return f"~staff_reset:u{uid}"


def _token_hash(token: str) -> str:
    key = (get_settings().secret_key or "").encode()
    return hmac.new(key, f"staff_reset|{token}".encode(), hashlib.sha256).hexdigest()


def _staff_reset_paused(db: Session, ip: str | None, now: datetime) -> bool:
    if not ip:
        return False
    since = now - timedelta(minutes=HELP_WINDOW_MINUTES)
    n = db.scalar(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.username == STAFF_RESET_MARK, LoginAttempt.ip == ip,
        LoginAttempt.success.is_(False), LoginAttempt.at >= since)) or 0
    return n >= HELP_IP_MAX_FAILURES


def _open_staff_resets(db: Session, user_id: int, now: datetime):
    return db.scalars(select(StaffPasswordReset).where(
        StaffPasswordReset.user_id == user_id, StaffPasswordReset.used_at.is_(None),
        StaffPasswordReset.revoked_at.is_(None))).all()


def find_staff_reset(db: Session, token: str | None) -> StaffPasswordReset | None:
    if not token:
        return None
    return db.scalar(select(StaffPasswordReset).where(StaffPasswordReset.token_hash == _token_hash(token)))


def staff_reset_status(db: Session, token: str | None, now: datetime | None = None) -> str:
    """Reason for a GET of the reset page: ok, missing, invalid, used, expired, revoked."""
    now = now or utcnow()
    if not token:
        return "missing"
    row = find_staff_reset(db, token)
    if row is None:
        return "invalid"
    if row.used_at is not None:
        return "used"
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at <= now:
        return "expired"
    u = db.get(User, row.user_id)
    if u is None or not u.active or u.role == "patient":
        return "revoked"
    return "ok"


def staff_forgot_password(db: Session, username: str | None, ip: str | None, lang: str = "en",
                          base_url: str = "", now: datetime | None = None) -> str:
    """Always "sent" (the page shows one generic message) or "paused". Never reveals whether an account exists.
    Typed username and email are not audited."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    uname = (username or "").strip().lower()[:80]
    if _staff_reset_paused(db, ip, now):
        audit.record(db, SYSTEM, "E", "signin_help.staff_reset_requested", entity_type="User", outcome="failure",
                     ts=now, detail={"reason": "rate_limited", "ip": ip, "language": lang})
        return "paused"
    db.add(LoginAttempt(username=STAFF_RESET_MARK, ip=ip, at=now, success=False))

    u = db.scalar(select(User).where(User.username == uname)) if uname else None
    reason, uid = "no_account", None
    if u is not None:
        uid = u.id
        db.add(LoginAttempt(username=_staff_reset_user_mark(u.id), ip=ip, at=now, success=False))
        if not u.active:
            reason = "inactive"
        elif u.role == "patient":
            reason = "patient_account"
        elif _prior_staff_reset_requests(db, u.id, now) >= HELP_RECORD_MAX_FAILURES:
            reason = "user_limited"
        else:
            reason = _issue_staff_reset(db, u, ip, lang, base_url, now)

    audit.record(db, SYSTEM, "C" if reason == "sent" else "E", "signin_help.staff_reset_requested",
                 entity_type="User", entity_id=None if reason in ("no_account", "patient_account") else uid,
                 ts=now, outcome="success" if reason == "sent" else "failure",
                 detail={"reason": reason, "ip": ip, "language": lang})
    return "sent"


def _prior_staff_reset_requests(db: Session, uid: int, now: datetime) -> int:
    """Attempts for this user in the lock window, excluding the row just recorded at `now`."""
    since = now - timedelta(hours=HELP_RECORD_LOCK_HOURS)
    return db.scalar(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.username == _staff_reset_user_mark(uid),
        LoginAttempt.success.is_(False), LoginAttempt.at >= since, LoginAttempt.at < now)) or 0


def _issue_staff_reset(db: Session, u: User, ip: str | None, lang: str, base_url: str, now: datetime) -> str:
    from ..calendar_uae import to_local
    from ..comms import mail
    from ..comms.signin_text import STAFF_RESET_TEXT

    addr = (u.email or "").strip()
    if not addr:
        return "no_email"
    if mail.is_synthetic_address(addr):
        return "placeholder"
    if not mail.configured():
        return "mail_off"
    if not mail.can_send_to_address(addr):
        return "not_deliverable"
    for old in _open_staff_resets(db, u.id, now):
        old.revoked_at, old.revoke_reason = now, "replaced by a new link"
    token = secrets.token_urlsafe(24)
    row = StaffPasswordReset(user_id=u.id, token_hash=_token_hash(token), created_at=now,
                             expires_at=now + timedelta(minutes=STAFF_RESET_TTL_MINUTES), ip=ip, language=lang)
    db.add(row)
    db.flush()
    texts = STAFF_RESET_TEXT[lang if lang in STAFF_RESET_TEXT else "en"]
    exp = to_local(row.expires_at).strftime("%d/%m/%Y %H:%M") + " UAE"
    link = (base_url or "").rstrip("/") + "/staff-reset-password?t=" + token
    body = texts["mail_body"].format(name=u.display_name or "", username=u.username, link=link, expires=exp)
    try:
        mail.send_email(addr, texts["mail_subject"], body)
        return "sent"
    except mail.MailError as e:
        log.warning("staff password-reset email not sent: %s", type(e).__name__)
        return "mail_failed"


def complete_staff_reset(db: Session, *, token: str | None, password: str, confirm: str, ip: str | None,
                         lang: str = "en", now: datetime | None = None) -> tuple[User | None, str]:
    """Returns (user, "ok") or (None, reason). reason in: rate_limited, invalid, expired, used, revoked,
    mismatch, weak:<problem>."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    actor = Actor(user_id=None, role="anonymous", display="staff (password reset page)", is_human=True)

    def fail(reason: str, row: StaffPasswordReset | None = None) -> tuple[None, str]:
        if reason not in ("mismatch",) and not reason.startswith("weak:"):
            db.add(LoginAttempt(username=STAFF_RESET_MARK, ip=ip, at=now, success=False))
        audit.record(db, actor, "E", "signin_help.staff_reset_failed", entity_type="User",
                     entity_id=row.user_id if row else None, ts=now, outcome="failure",
                     detail={"reason": reason.split(":")[0], "ip": ip, "language": lang})
        return None, reason

    if _staff_reset_paused(db, ip, now):
        audit.record(db, actor, "E", "signin_help.staff_reset_failed", entity_type="User", outcome="failure",
                     ts=now, detail={"reason": "rate_limited", "ip": ip, "language": lang})
        return None, "rate_limited"
    row = find_staff_reset(db, token)
    if row is None:
        return fail("invalid")
    if row.used_at is not None:
        return fail("used", row)
    if row.revoked_at is not None:
        return fail("revoked", row)
    if row.expires_at <= now:
        return fail("expired", row)
    u = db.get(User, row.user_id)
    if u is None or not u.active or u.role == "patient":
        return fail("revoked", row)
    if password != confirm:
        return None, "mismatch"
    probs = accounts.password_problems(password, u.username, u.display_name)
    if probs:
        return None, "weak:" + probs[0]
    accounts.set_password(u, password, must_change=False)
    accounts.revoke_user_sessions(db, u.id)
    row.used_at = now
    db.add(LoginAttempt(username=u.username, ip=ip, at=now, success=True))
    notify = _notify_password_changed(db, u, lang)
    audit.record(db, actor, "U", "user.password_reset", entity_type="User", entity_id=u.id, ts=now,
                 detail={"method": "email_link", "sessions_ended": True, "forced_change": False,
                         "notify_sent": notify})
    return u, "ok"


def _notify_password_changed(db: Session, u: User, lang: str) -> bool:
    from ..comms import mail
    from ..comms.signin_text import STAFF_RESET_TEXT

    if not mail.can_send_to_address(u.email):
        return False
    texts = STAFF_RESET_TEXT[lang if lang in STAFF_RESET_TEXT else "en"]
    try:
        mail.send_email(u.email, texts["changed_subject"],
                        texts["changed_body"].format(name=u.display_name or "", username=u.username))
        return True
    except mail.MailError as e:
        log.warning("staff password-changed email not sent: %s", type(e).__name__)
        audit.record(db, SYSTEM, "E", "signin_help.staff_reset_notify_failed", entity_type="User",
                     entity_id=u.id, outcome="failure", detail={"reason": type(e).__name__})
        return False


# ------------------------------------------------------------------ patient email reset
def _patient_reset_user_mark(pid: int) -> str:
    return f"~patient_reset:p{pid}"


def _patient_token_hash(token: str) -> str:
    key = (get_settings().secret_key or "").encode()
    return hmac.new(key, f"patient_reset|{token}".encode(), hashlib.sha256).hexdigest()


def _patient_reset_paused(db: Session, ip: str | None, now: datetime) -> bool:
    if not ip:
        return False
    since = now - timedelta(minutes=HELP_WINDOW_MINUTES)
    n = db.scalar(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.username == PATIENT_RESET_MARK, LoginAttempt.ip == ip,
        LoginAttempt.success.is_(False), LoginAttempt.at >= since)) or 0
    return n >= HELP_IP_MAX_FAILURES


def _prior_patient_reset_requests(db: Session, pid: int, now: datetime) -> int:
    """Attempts for this patient in the lock window, excluding the row just recorded at `now`."""
    since = now - timedelta(hours=HELP_RECORD_LOCK_HOURS)
    return db.scalar(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.username == _patient_reset_user_mark(pid),
        LoginAttempt.success.is_(False), LoginAttempt.at >= since, LoginAttempt.at < now)) or 0


def _open_patient_resets(db: Session, user_id: int):
    return db.scalars(select(PatientPasswordReset).where(
        PatientPasswordReset.user_id == user_id, PatientPasswordReset.used_at.is_(None),
        PatientPasswordReset.revoked_at.is_(None))).all()


def find_patient_reset(db: Session, token: str | None) -> PatientPasswordReset | None:
    if not token:
        return None
    return db.scalar(select(PatientPasswordReset).where(PatientPasswordReset.token_hash == _patient_token_hash(token)))


def patient_reset_status(db: Session, token: str | None, now: datetime | None = None) -> str:
    """Reason for a GET of the reset page: ok, missing, invalid, used, expired, revoked."""
    now = now or utcnow()
    if not token:
        return "missing"
    row = find_patient_reset(db, token)
    if row is None:
        return "invalid"
    if row.used_at is not None:
        return "used"
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at <= now:
        return "expired"
    u = db.get(User, row.user_id)
    if u is None or not u.active or u.role != "patient":
        return "revoked"
    return "ok"


def _patient_mail_lang(p: Patient | None, u: User | None, page_lang: str) -> str:
    from ..comms.signin_text import PATIENT_RESET_TEXT

    for cand in ((u.ui_language if u is not None else None), (p.language if p is not None else None), page_lang):
        if cand in PATIENT_RESET_TEXT:
            return cand
    return "en"


def _patient_mail_block_reason(p: Patient) -> str | None:
    """None if the patient can be emailed a reset or sign-up link; else an audit reason."""
    from ..comms import mail

    addr = (p.email or "").strip()
    if not addr:
        return "no_email"
    if mail.is_synthetic_address(addr):
        return "placeholder"
    if not mail.configured():
        return "mail_off"
    if not mail.can_send_to_patient(p):
        return "not_deliverable"
    return None


def patient_forgot_password_by_username(db: Session, username: str | None, ip: str | None, lang: str = "en",
                                        base_url: str = "", now: datetime | None = None) -> str:
    """Always "sent" (the page shows one generic message) or "paused". Never reveals whether an account exists.
    Typed username and email are not audited."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    uname = (username or "").strip().lower()[:80]
    if _patient_reset_paused(db, ip, now):
        audit.record(db, SYSTEM, "E", "signin_help.patient_reset_requested", entity_type="Patient", outcome="failure",
                     ts=now, detail={"reason": "rate_limited", "ip": ip, "language": lang, "path": "username"})
        return "paused"
    db.add(LoginAttempt(username=PATIENT_RESET_MARK, ip=ip, at=now, success=False))

    u = db.scalar(select(User).where(User.username == uname)) if uname else None
    reason, pid = "no_account", None
    if u is not None:
        if u.role != "patient" or u.patient_id is None:
            reason = "staff_account"
        else:
            pid = u.patient_id
            db.add(LoginAttempt(username=_patient_reset_user_mark(u.patient_id), ip=ip, at=now, success=False))
            p = db.get(Patient, u.patient_id)
            if p is None or p.deceased:
                reason = "no_account"
                pid = None
            elif not u.active:
                reason = "inactive"
            elif _prior_patient_reset_requests(db, u.patient_id, now) >= HELP_RECORD_MAX_FAILURES:
                reason = "user_limited"
            elif not u.password_hash:
                reason = _issue_patient_signup(db, p, u, ip, lang, base_url, now)
            else:
                reason = _issue_patient_reset(db, p, u, ip, lang, base_url, now, remind_username=False)

    audit.record(db, SYSTEM, "C" if reason in ("sent", "invite_sent") else "E",
                 "signin_help.patient_reset_requested",
                 entity_type="Patient", entity_id=None if reason in ("no_account", "staff_account") else pid,
                 ts=now, outcome="success" if reason in ("sent", "invite_sent") else "failure",
                 detail={"reason": reason, "ip": ip, "language": lang, "path": "username"})
    return "sent"


def patient_forgot_password_by_details(db: Session, birth_date, id_last4, phone, ip, lang="en",
                                       base_url: str = "", now=None) -> str:
    """Always "sent" or "paused". A 3-factor match emails a reset link (and the username) or, if the patient
    has no app password yet, a sign-up link. Typed identity values are not audited."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    if _patient_reset_paused(db, ip, now):
        audit.record(db, SYSTEM, "E", "signin_help.patient_reset_requested", entity_type="Patient", outcome="failure",
                     ts=now, detail={"reason": "rate_limited", "ip": ip, "language": lang, "path": "details"})
        return "paused"
    db.add(LoginAttempt(username=PATIENT_RESET_MARK, ip=ip, at=now, success=False))

    reason, p, u, near = _identify_patient(db, birth_date, id_last4, phone, now)
    pid = p.id if p is not None else None
    if reason in ("no_match", "ambiguous", "record_locked"):
        db.add(LoginAttempt(username=HELP_MARK, ip=ip, at=now, success=False))
        for np in near:
            db.add(LoginAttempt(username=_record_mark(np.id), ip=ip, at=now, success=False))
    elif p is not None:
        db.add(LoginAttempt(username=_patient_reset_user_mark(p.id), ip=ip, at=now, success=False))
        if _prior_patient_reset_requests(db, p.id, now) >= HELP_RECORD_MAX_FAILURES:
            reason = "user_limited"
        elif reason == "ok":
            reason = _issue_patient_reset(db, p, u, ip, lang, base_url, now, remind_username=True)
        elif reason in ("no_active_account", "inactive") and (u is None or not u.active):
            if u is not None and not u.active:
                reason = "inactive"
            else:
                reason = _issue_patient_signup(db, p, u, ip, lang, base_url, now)
        elif reason == "no_active_account":
            reason = _issue_patient_signup(db, p, u, ip, lang, base_url, now)

    audit.record(db, SYSTEM, "C" if reason in ("sent", "invite_sent") else "E",
                 "signin_help.patient_reset_requested",
                 entity_type="Patient",
                 entity_id=None if reason in ("no_match", "ambiguous", "no_account", "staff_account") else pid,
                 ts=now, outcome="success" if reason in ("sent", "invite_sent") else "failure",
                 detail={"reason": reason, "ip": ip, "language": lang, "path": "details",
                         "near_miss_records": len(near)})
    return "sent"


def _issue_patient_reset(db: Session, p: Patient, u: User, ip: str | None, lang: str, base_url: str,
                         now: datetime, *, remind_username: bool) -> str:
    from ..calendar_uae import to_local
    from ..comms import mail
    from ..comms.signin_text import PATIENT_RESET_TEXT

    block = _patient_mail_block_reason(p)
    if block:
        return block
    for old in _open_patient_resets(db, u.id):
        old.revoked_at, old.revoke_reason = now, "replaced by a new link"
    token = secrets.token_urlsafe(24)
    mail_lang = _patient_mail_lang(p, u, lang)
    row = PatientPasswordReset(user_id=u.id, patient_id=p.id, token_hash=_patient_token_hash(token),
                               created_at=now, expires_at=now + timedelta(minutes=PATIENT_RESET_TTL_MINUTES),
                               ip=ip, language=mail_lang)
    db.add(row)
    db.flush()
    texts = PATIENT_RESET_TEXT[mail_lang]
    exp = to_local(row.expires_at).strftime("%d/%m/%Y %H:%M") + " UAE"
    link = (base_url or "").rstrip("/") + "/patient-reset-password?t=" + token
    body_key = "mail_body_details" if remind_username else "mail_body"
    name = (p.given_name or u.display_name or "").strip()
    body = texts[body_key].format(name=name, username=u.username, link=link, expires=exp)
    try:
        mail.send_email(p.email, texts["mail_subject"], body)
        return "sent"
    except mail.MailError as e:
        log.warning("patient password-reset email not sent: %s", type(e).__name__)
        return "mail_failed"


def _issue_patient_signup(db: Session, p: Patient, u: User | None, ip: str | None, lang: str,
                          base_url: str, now: datetime) -> str:
    """Matched patient has no app password yet: email the existing sign-up / activation invite."""
    from . import invites as inv

    block = _patient_mail_block_reason(p)
    if block:
        return block
    if p.birth_date is None:
        return "no_dob"
    try:
        sent = inv.issue_self_service_signup(db, p, user=u, base_url=base_url, lang=lang, now=now, actor=SYSTEM)
        return "invite_sent" if sent else "invite_failed"
    except Exception as e:  # noqa: BLE001 - the public page must stay generic
        log.warning("patient sign-up email from sign-in help not sent: %s", type(e).__name__)
        return "invite_failed"


def complete_patient_reset(db: Session, *, token: str | None, password: str, confirm: str, ip: str | None,
                           lang: str = "en", now: datetime | None = None) -> tuple[User | None, str]:
    """Returns (user, "ok") or (None, reason). reason in: rate_limited, invalid, expired, used, revoked,
    mismatch, weak:<problem>."""
    now = now or utcnow()
    ip = (ip or "")[:64] or None
    actor = Actor(user_id=None, role="anonymous", display="patient (password reset page)", is_human=True)

    def fail(reason: str, row: PatientPasswordReset | None = None) -> tuple[None, str]:
        if reason not in ("mismatch",) and not reason.startswith("weak:"):
            db.add(LoginAttempt(username=PATIENT_RESET_MARK, ip=ip, at=now, success=False))
        audit.record(db, actor, "E", "signin_help.patient_reset_failed", entity_type="User",
                     entity_id=row.user_id if row else None, ts=now, outcome="failure",
                     detail={"reason": reason.split(":")[0], "ip": ip, "language": lang})
        return None, reason

    if _patient_reset_paused(db, ip, now):
        audit.record(db, actor, "E", "signin_help.patient_reset_failed", entity_type="User", outcome="failure",
                     ts=now, detail={"reason": "rate_limited", "ip": ip, "language": lang})
        return None, "rate_limited"
    row = find_patient_reset(db, token)
    if row is None:
        return fail("invalid")
    if row.used_at is not None:
        return fail("used", row)
    if row.revoked_at is not None:
        return fail("revoked", row)
    if row.expires_at <= now:
        return fail("expired", row)
    u = db.get(User, row.user_id)
    if u is None or not u.active or u.role != "patient":
        return fail("revoked", row)
    if password != confirm:
        return None, "mismatch"
    probs = accounts.password_problems(password, u.username, u.display_name)
    if probs:
        return None, "weak:" + probs[0]
    accounts.set_password(u, password, must_change=False)
    accounts.revoke_user_sessions(db, u.id)
    row.used_at = now
    for other in _open_patient_resets(db, u.id):
        if other.id != row.id:
            other.revoked_at, other.revoke_reason = now, "password already reset"
    db.add(LoginAttempt(username=u.username, ip=ip, at=now, success=True))
    p = db.get(Patient, u.patient_id) if u.patient_id else None
    notify = _notify_patient_password_changed(db, p, u, lang)
    open_req = open_patient_request(db, u.patient_id) if u.patient_id else None
    if open_req is not None:
        open_req.status, open_req.handled_at, open_req.handled_by = "done", now, actor.display
        open_req.outcome_note = "Patient set a new password from an emailed link"
    audit.record(db, actor, "U", "patient.password_reset", entity_type="User", entity_id=u.id, ts=now,
                 detail={"method": "email_link", "patient_id": u.patient_id, "sessions_ended": True,
                         "forced_change": False, "notify_sent": notify})
    return u, "ok"


def _notify_patient_password_changed(db: Session, p: Patient | None, u: User, lang: str) -> bool:
    from ..comms import mail
    from ..comms.signin_text import PATIENT_RESET_TEXT

    if p is None or not mail.can_send_to_patient(p):
        return False
    texts = PATIENT_RESET_TEXT[_patient_mail_lang(p, u, lang)]
    name = (p.given_name or u.display_name or "").strip()
    try:
        mail.send_email(p.email, texts["changed_subject"],
                        texts["changed_body"].format(name=name, username=u.username))
        return True
    except mail.MailError as e:
        log.warning("patient password-changed email not sent: %s", type(e).__name__)
        audit.record(db, SYSTEM, "E", "signin_help.patient_reset_notify_failed", entity_type="User",
                     entity_id=u.id, outcome="failure", detail={"reason": type(e).__name__})
        return False
