"""Patient Forgot password emails a one-time reset link (username or identity details)."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from kareshafaa import accounts
from kareshafaa.comms import mail
from kareshafaa.comms.signin_text import HELP_TEXT, PATIENT_RESET_TEXT
from kareshafaa.config import get_settings
from kareshafaa.models import AuditEvent, Patient, PatientInvite, PatientPasswordReset, SigninHelpRequest, User, utcnow
from kareshafaa.services import signin_help as sh
from kareshafaa.synth.test_mailboxes import PATIENT_MAILBOXES
from tests.test_signin_help import NEW_PW, PW, csrf, fresh, help_post, result, _f, _login

GENERIC = HELP_TEXT["en"]["p_generic_reset"]
GENERIC_DETAILS = HELP_TEXT["en"]["p_generic_reset_details"]
FAKE_SECRET = "not-a-real-secret-for-tests"


@pytest.fixture()
def mail_on(monkeypatch):
    """Mailbox looks configured; send_email is mocked (no network)."""
    s = get_settings()
    monkeypatch.setattr(s, "smtp_password", FAKE_SECRET)
    monkeypatch.setattr(s, "mail_from", "kareshafaa_com@outlook.com")
    monkeypatch.setattr(s, "smtp_host", None)
    monkeypatch.setattr(s, "alert_to", None)
    monkeypatch.setattr(s, "mail_allow_synthetic", False)
    monkeypatch.setattr(s, "mail_oauth_client_id", None)
    monkeypatch.setattr(s, "mail_oauth_refresh_token", None)
    monkeypatch.setattr(s, "mail_transport", "auto")
    monkeypatch.setattr(s, "public_base_url", "https://kareshafaa.onrender.com")
    sent: list[tuple] = []

    def fake_send(to, subject, body, **kw):
        sent.append((to, subject, body, kw))

    monkeypatch.setattr(mail, "send_email", fake_send)
    return sent


_n = 0


@pytest.fixture()
def pat(db, make_patient):
    import random
    global _n
    _n += 1
    pid = make_patient(language="ar")
    p = db.get(Patient, pid)
    p.birth_date = date(1940, 1, 1) + timedelta(days=random.randint(0, 20000))
    eid_tail = f"{random.randint(0, 9999):04d}"
    p.national_id = f"784-{p.birth_date.year}-{random.randint(0, 9999999):07d}"[:-4] + eid_tail[:3] + "-" + eid_tail[3]
    p.phone = f"+97150{random.randint(0, 9999999):07d}"
    p.phone_valid = True
    p.email = f"help.patient{_n}@clinic.ae"
    u = User(username=f"p.reset{pid}", display_name="Help Patient", role="patient", patient_id=pid, active=True)
    accounts.set_password(u, PW)
    db.add(u)
    db.commit()
    digits = re.sub(r"\D", "", p.national_id)
    return {"pid": pid, "user": u.username, "uid": u.id, "email": p.email, "dob": p.birth_date.isoformat(),
            "last4": digits[-4:], "phone": "0" + p.phone[4:], "e164": p.phone, "lang": p.language}


def _ask_user(c, username, lang="en"):
    tok = csrf(c, f"/forgot-password?lang={lang}&who=patient")
    return c.post("/forgot-password", data={"csrf_token": tok, "lang": lang, "who": "patient", "username": username})


def _ask_details(c, pat, lang="en", **kw):
    return help_post(c, "password", lang=lang, via="details", **_f(pat, **kw))


def _token(sent) -> str:
    return re.search(r"/patient-reset-password\?t=([A-Za-z0-9_-]+)", sent[0][2]).group(1)


def _set(c, token, password=NEW_PW, confirm=None, lang="en"):
    tok = csrf(c, "/login")
    return c.post("/patient-reset-password", data={"csrf_token": tok, "lang": lang, "t": token,
                                                   "password": password, "confirm": password if confirm is None else confirm})


def test_patient_forgot_password_page_is_username_form(app):
    html = fresh(app).get("/forgot-password?who=patient&lang=en").text
    assert 'id="username"' in html and 'id="birth_date"' not in html
    assert HELP_TEXT["en"]["p_pw_intro"] in html
    assert 'href="/forgot-password?lang=en&who=patient&via=details"' in html
    assert 'href="/forgot-password?lang=en&who=patient&via=admin"' in html
    details = fresh(app).get("/forgot-password?who=patient&via=details&lang=en").text
    assert 'id="birth_date"' in details and HELP_TEXT["en"]["p_details_intro"] in details


def test_username_path_generic_response_for_unknown_and_known(app, db, mail_on, pat):
    known = _ask_user(fresh(app), pat["user"])
    unknown = _ask_user(fresh(app), "no.such.patient.user")
    typo = _ask_user(fresh(app), pat["user"].upper())
    assert known.status_code == unknown.status_code == 200
    assert result(known.text) == result(unknown.text) == result(typo.text) == GENERIC
    assert 'data-result="patient_reset_sent"' in known.text
    assert pat["user"] not in unknown.text
    assert pat["email"] not in known.text
    assert len(mail_on) == 2
    evs = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")
                     .order_by(AuditEvent.id)).all()
    reasons = [e.detail.get("reason") for e in evs]
    assert "sent" in reasons and "no_account" in reasons
    blob = " ".join((e.detail or {}).__repr__() + (e.entity_id or "") for e in evs)
    assert "no.such.patient.user" not in blob and pat["email"] not in blob


def test_username_path_email_has_absolute_link_and_uses_patient_language(app, db, mail_on, pat):
    r = _ask_user(fresh(app), pat["user"], lang="en")  # page in English; patient language is Arabic
    assert r.status_code == 200 and result(r.text) == GENERIC
    assert len(mail_on) == 1
    to, subject, body, _kw = mail_on[0]
    assert to == pat["email"]
    assert subject == PATIENT_RESET_TEXT["ar"]["mail_subject"]
    assert "https://kareshafaa.onrender.com/patient-reset-password?t=" in body
    assert pat["user"] in body
    assert "UAE" in body
    token = _token(mail_on)
    row = db.scalars(select(PatientPasswordReset).where(PatientPasswordReset.user_id == pat["uid"])).one()
    assert row.used_at is None and row.revoked_at is None and row.patient_id == pat["pid"]
    assert row.expires_at - row.created_at == timedelta(minutes=sh.PATIENT_RESET_TTL_MINUTES)
    assert token not in (row.token_hash or "")
    assert db.scalar(select(PatientPasswordReset.id).where(PatientPasswordReset.token_hash == token)) is None


def test_details_path_emails_reset_and_reminds_username(app, db, mail_on, pat):
    ok = _ask_details(fresh(app), pat, lang="hi")
    miss = _ask_details(fresh(app), pat, lang="hi", phone="0500000000")
    assert ok.status_code == miss.status_code == 200
    assert result(ok.text) == result(miss.text) == HELP_TEXT["hi"]["p_generic_reset_details"]
    assert pat["user"] not in ok.text and pat["email"] not in ok.text
    assert len(mail_on) == 1
    _to, subject, body, _kw = mail_on[0]
    assert _to == pat["email"]
    assert subject == PATIENT_RESET_TEXT["ar"]["mail_subject"]  # stored patient language
    assert pat["user"] in body
    assert "https://kareshafaa.onrender.com/patient-reset-password?t=" in body
    # a no-match does not open a coordinator request or leave a token
    assert db.scalar(select(SigninHelpRequest.id).where(SigninHelpRequest.patient_id == pat["pid"])) is None
    evs = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")
                     .order_by(AuditEvent.id)).all()
    reasons = [e.detail.get("reason") for e in evs]
    assert "sent" in reasons and "no_match" in reasons


def test_details_path_sends_signup_invite_when_there_is_no_app_password(app, db, mail_on, make_patient):
    import random
    pid = make_patient(language="en")
    p = db.get(Patient, pid)
    p.birth_date = date(1982, 3, 4)
    p.national_id = "784-1982-1234567-8"
    p.phone = "+971501112233"
    p.phone_valid = True
    p.email = "needs.signup@clinic.ae"
    db.commit()
    digits = re.sub(r"\D", "", p.national_id)
    facts = {"birth_date": "1982-03-04", "id_type": "emirates_id", "id_last4": digits[-4:], "phone": "050 111 2233"}
    r = help_post(fresh(app), "password", via="details", **facts)
    assert r.status_code == 200 and result(r.text) == GENERIC_DETAILS
    assert len(mail_on) == 1
    to, subject, body, _kw = mail_on[0]
    assert to == "needs.signup@clinic.ae"
    assert subject == PATIENT_RESET_TEXT["en"]["signup_subject"]
    assert "/activate?t=" in body
    assert "/patient-reset-password" not in body
    inv = db.scalars(select(PatientInvite).where(PatientInvite.patient_id == pid)).one()
    assert inv.purpose == "activate" and inv.channel == "email" and inv.used_at is None
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev.detail.get("reason") == "invite_sent"
    # suggested username is mentioned, and a user row now exists without a password
    u = db.scalar(select(User).where(User.patient_id == pid, User.role == "patient"))
    assert u is not None and not u.password_hash and u.username in body


def test_token_is_single_use_expires_and_is_replaced(app, db, mail_on, pat):
    _ask_user(fresh(app), pat["user"])
    first = _token(mail_on)
    mail_on.clear()
    _ask_user(fresh(app), pat["user"])
    second = _token(mail_on)
    assert first != second
    db.expire_all()
    rows = db.scalars(select(PatientPasswordReset).where(PatientPasswordReset.user_id == pat["uid"])
                      .order_by(PatientPasswordReset.id)).all()
    assert len(rows) == 2 and rows[0].revoked_at is not None and rows[1].revoked_at is None
    assert PATIENT_RESET_TEXT["en"]["err"]["revoked"] in fresh(app).get(f"/patient-reset-password?t={first}").text
    assert _set(fresh(app), first).status_code == 400
    ok = _set(fresh(app), second)
    assert ok.status_code == 200 and PATIENT_RESET_TEXT["en"]["done_title"] in ok.text
    again = _set(fresh(app), second)
    assert again.status_code == 400 and PATIENT_RESET_TEXT["en"]["err"]["used"] in again.text
    mail_on.clear()
    _ask_user(fresh(app), pat["user"])
    third = _token(mail_on)
    db.expire_all()
    row = db.scalars(select(PatientPasswordReset).where(PatientPasswordReset.user_id == pat["uid"])
                     .order_by(PatientPasswordReset.id.desc())).first()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    expired = _set(fresh(app), third)
    assert expired.status_code == 400 and PATIENT_RESET_TEXT["en"]["err"]["expired"] in expired.text


def test_reset_sets_password_login_works_and_sessions_end(app, db, mail_on, pat):
    s1 = fresh(app)
    assert _login(s1, pat["user"], PW).status_code == 303
    _ask_user(fresh(app), pat["user"])
    r = _set(fresh(app), _token(mail_on))
    assert r.status_code == 200 and pat["user"] in r.text
    assert s1.get("/api/auth/me").status_code == 401
    assert _login(fresh(app), pat["user"], PW).status_code == 401
    ok = _login(fresh(app), pat["user"], NEW_PW)
    assert ok.status_code == 303 and "/account/password" not in ok.headers.get("location", "")
    db.expire_all()
    u = db.get(User, pat["uid"])
    assert u.must_change_password is False
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "patient.password_reset",
                                             AuditEvent.entity_id == str(pat["uid"])).order_by(AuditEvent.id.desc())).first()
    assert ev is not None and ev.detail.get("method") == "email_link" and ev.detail.get("sessions_ended") is True
    assert ev.detail.get("forced_change") is False and ev.detail.get("notify_sent") is True
    assert any(m[0] == pat["email"] and m[1] == PATIENT_RESET_TEXT["ar"]["changed_subject"] for m in mail_on)
    assert NEW_PW not in repr(db.scalars(select(AuditEvent.detail).where(AuditEvent.entity_id == str(pat["uid"]))).all())


def test_rate_limit_by_ip_and_per_account(app, db, mail_on, pat):
    c = fresh(app)
    for _ in range(sh.HELP_IP_MAX_FAILURES):
        r = _ask_user(c, "unknown.device.user")
        assert r.status_code == 200 and result(r.text) == GENERIC
    paused = _ask_user(c, pat["user"])
    assert paused.status_code == 429 and HELP_TEXT["en"]["paused"] in paused.text
    assert all(m[0] != pat["email"] for m in mail_on)

    mail_on.clear()
    for _ in range(sh.HELP_RECORD_MAX_FAILURES):
        r = _ask_user(fresh(app), pat["user"])
        assert r.status_code == 200 and result(r.text) == GENERIC
    assert len(mail_on) == sh.HELP_RECORD_MAX_FAILURES
    mail_on.clear()
    limited = _ask_user(fresh(app), pat["user"])
    assert limited.status_code == 200 and result(limited.text) == GENERIC
    assert mail_on == []
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev.detail.get("reason") == "user_limited"


def test_placeholder_and_missing_email_skip_send_and_offer_admin_fallback(app, db, mail_on, make_patient):
    import random

    def _acct(email):
        pid = make_patient()
        p = db.get(Patient, pid)
        p.birth_date = date(1975, 6, 6)
        p.national_id = f"784-1975-{random.randint(0, 9999999):07d}-1"
        p.phone = f"+97150{random.randint(0, 9999999):07d}"
        p.email = email
        u = User(username=f"p.nomail{pid}", display_name="No Mail", role="patient", patient_id=pid, active=True)
        accounts.set_password(u, PW)
        db.add(u)
        db.commit()
        return u

    placeholder = _acct("sara.patient.1@example.com")
    empty = _acct(None)
    for u in (placeholder, empty):
        r = _ask_user(fresh(app), u.username)
        assert r.status_code == 200 and result(r.text) == GENERIC
    assert mail_on == []
    reasons = [e.detail.get("reason") for e in db.scalars(
        select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")).all()]
    assert "placeholder" in reasons and "no_email" in reasons
    # fallback still opens a coordinator request
    p = db.get(Patient, empty.patient_id)
    digits = re.sub(r"\D", "", p.national_id or "")
    facts = {"birth_date": p.birth_date.isoformat(), "id_type": "emirates_id", "id_last4": digits[-4:],
             "phone": "0" + p.phone[4:]}
    r = help_post(fresh(app), "password", via="admin", **facts)
    assert r.status_code == 200 and result(r.text) == HELP_TEXT["en"]["generic_password"]
    assert db.scalar(select(SigninHelpRequest.id).where(SigninHelpRequest.patient_id == empty.patient_id))


def test_mail_failure_is_still_generic_and_audited(app, db, mail_on, pat, monkeypatch):
    def boom(*a, **k):
        raise mail.MailError("server refused")

    monkeypatch.setattr(mail, "send_email", boom)
    r = _ask_user(fresh(app), pat["user"])
    assert r.status_code == 200 and result(r.text) == GENERIC
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev.outcome == "failure" and ev.detail.get("reason") == "mail_failed"
    assert ev.entity_id == str(pat["pid"])


def test_inactive_and_staff_accounts_do_not_get_mail(app, db, mail_on, pat):
    u = db.get(User, pat["uid"])
    u.active = False
    staff = User(username="staff.on.patient.form", display_name="Staff", role="gp", active=True,
                 email="staff.reset@clinic.ae")
    accounts.set_password(staff, PW)
    db.add(staff)
    db.commit()
    r = _ask_user(fresh(app), pat["user"])
    s = _ask_user(fresh(app), staff.username)
    assert result(r.text) == result(s.text) == GENERIC
    assert mail_on == []
    reasons = [e.detail.get("reason") for e in db.scalars(
        select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")).all()]
    assert "inactive" in reasons and "staff_account" in reasons


def test_syn_sample_is_blocked_unless_allowlisted(app, db, mail_on, pat):
    p = db.get(Patient, pat["pid"])
    p.mrn = f"SYN-{p.id}-mail"
    db.commit()
    r = _ask_user(fresh(app), pat["user"])
    assert result(r.text) == GENERIC
    assert mail_on == []
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev.detail.get("reason") == "not_deliverable"
    # allowlisted test inbox on a SYN- record is emailed
    p.email = PATIENT_MAILBOXES["patient.en"]
    db.commit()
    r = _ask_user(fresh(app), pat["user"])
    assert result(r.text) == GENERIC
    assert len(mail_on) == 1 and mail_on[0][0] == PATIENT_MAILBOXES["patient.en"]


def test_empty_username_is_a_field_error_not_generic(app):
    r = _ask_user(fresh(app), "   ")
    assert r.status_code == 400 and HELP_TEXT["en"]["p_err_username"] in r.text
    assert 'data-result="patient_reset_sent"' not in r.text
