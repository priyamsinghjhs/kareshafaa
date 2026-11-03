"""Sign-in help: "Forgot username?" and "Forgot password?" from the sign-in page (added 3 Nov 2026).

There is no e-mail or SMS provider (patient messages are simulated), so nothing is sent automatically:

* PATIENTS prove who they are with three facts we hold: date of birth, the last 4 characters of their identity
  document (Emirates ID or passport / other ID) and their registered mobile number.
  - Forgot username: on a match the username is shown on the page (patients have no e-mail). Otherwise a generic
    message. SHOW_USERNAME_ON_MATCH is a CONFIRM decision for the clinical lead.
  - Forgot password: on a match a reset request goes to the care coordinators; the page always shows the same
    message. A coordinator checks who the patient is and sends a one-time RESET code (the activation-code mechanism
    in services/invites.py: single use, expiry, lockout, revoke). The patient sets a new password on
    /reset-password with the code and their date of birth; every existing session ends.
  Responses never reveal whether an account exists. Repeated failures pause the device, and repeated near-misses
  on one record silently stop that record answering for a while. Every attempt is audited (no typed values).
* STAFF are told to contact the system administrator and may leave a request (name, role, organisation,
  contact). Only administrators see it (Admin > Users); they reset the password there (temporary password shown
  once, change forced at next sign-in, all sessions ended).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import LoginAttempt, Patient, SigninHelpRequest, User, utcnow
from ..security import Actor
from ..sla import due_at as sla_due_at
from ..sla import rag_status
from .errors import Forbidden, Invalid, NotFound

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
    user, reason, pid = None, "no_match", None
    if len(full) == 1:
        p = full[0]
        pid = p.id
        u = db.scalar(select(User).where(User.patient_id == p.id, User.role == "patient").order_by(User.id).limit(1))
        if _record_locked(db, p.id, now):
            reason = "record_locked"
        elif u is None or not u.active or not u.password_hash:
            reason = "no_active_account"  # the page still answers generically (activation is a care-team task)
        else:
            user, reason = u, "ok"
    elif len(full) > 1:
        reason = "ambiguous"
    if user is None:
        db.add(LoginAttempt(username=HELP_MARK, ip=ip, at=now, success=False))
        for p in near:  # one fact right, one wrong: count against that record
            db.add(LoginAttempt(username=_record_mark(p.id), ip=ip, at=now, success=False))
    audit.record(db, SYSTEM, "E", event, entity_type="Patient", entity_id=pid if user is not None else None, ts=now,
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
    """"sent" (the page always says the same thing) | "paused". On a match a reset request reaches coordinators."""
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
