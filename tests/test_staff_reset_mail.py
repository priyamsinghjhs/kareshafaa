"""Staff Forgot password emails a one-time reset link instead of only creating an admin request."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from kareshafaa import accounts
from kareshafaa.comms import mail
from kareshafaa.comms.signin_text import HELP_TEXT, STAFF_RESET_TEXT
from kareshafaa.models import AuditEvent, Patient, SigninHelpRequest, StaffPasswordReset, User, utcnow
from kareshafaa.config import get_settings
from kareshafaa.services import signin_help as sh
from tests.test_signin_help import NEW_PW, PW, csrf, fresh, help_post, result, _f, _login, _staff

GENERIC = HELP_TEXT["en"]["s_generic_reset"]
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


def _make_staff(db, *, username=None, email="omar.reset@clinic.ae", role="gp", active=True, password=PW) -> User:
    global _n
    _n += 1
    n = (username or f"staff.reset{_n}").lower()
    u = User(username=n, display_name="Omar Haddad", role=role, active=active, email=email)
    accounts.set_password(u, password)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def pat(db, make_patient):
    import random
    pid = make_patient(language="ar")
    p = db.get(Patient, pid)
    p.birth_date = date(1940, 1, 1) + timedelta(days=random.randint(0, 20000))
    eid_tail = f"{random.randint(0, 9999):04d}"
    p.national_id = f"784-{p.birth_date.year}-{random.randint(0, 9999999):07d}"[:-4] + eid_tail[:3] + "-" + eid_tail[3]
    p.phone = f"+97150{random.randint(0, 9999999):07d}"
    p.phone_valid = True
    u = User(username=f"p.help{pid}", display_name="Help Patient", role="patient", patient_id=pid, active=True)
    accounts.set_password(u, PW)
    db.add(u)
    db.commit()
    digits = re.sub(r"\D", "", p.national_id)
    return {"pid": pid, "user": u.username, "uid": u.id, "dob": p.birth_date.isoformat(), "last4": digits[-4:],
            "phone": "0" + p.phone[4:], "e164": p.phone}


def _ask(c, username, lang="en"):
    tok = csrf(c, f"/forgot-password?lang={lang}&who=staff")
    return c.post("/forgot-password", data={"csrf_token": tok, "lang": lang, "who": "staff", "username": username})


def _token(sent) -> str:
    return re.search(r"/staff-reset-password\?t=([A-Za-z0-9_-]+)", sent[0][2]).group(1)


def _set(c, token, password=NEW_PW, confirm=None, lang="en"):
    tok = csrf(c, "/login")
    return c.post("/staff-reset-password", data={"csrf_token": tok, "lang": lang, "t": token,
                                                 "password": password, "confirm": password if confirm is None else confirm})


def test_staff_forgot_password_page_is_username_form(app):
    html = fresh(app).get("/forgot-password?who=staff&lang=en").text
    assert 'id="username"' in html and 'id="full_name"' not in html
    assert HELP_TEXT["en"]["s_pw_intro"] in html
    assert 'href="/forgot-password?lang=en&who=staff&via=admin"' in html
    assert HELP_TEXT["en"]["s_generic_reset"] not in html or 'id="help-form"' in html


def test_generic_response_for_unknown_and_known_usernames(app, db, mail_on):
    u = _make_staff(db)
    known = _ask(fresh(app), u.username)
    unknown = _ask(fresh(app), "no.such.staff.user")
    typo = _ask(fresh(app), u.username.upper())  # usernames are case-insensitive
    assert known.status_code == unknown.status_code == 200
    assert result(known.text) == result(unknown.text) == result(typo.text) == GENERIC
    assert 'data-result="staff_reset_sent"' in known.text
    assert u.username not in unknown.text
    assert u.email not in known.text
    assert len(mail_on) == 2  # known + case-insensitive repeat (typo of same account)
    evs = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.staff_reset_requested")
                     .order_by(AuditEvent.id)).all()
    reasons = [e.detail.get("reason") for e in evs]
    assert "sent" in reasons and "no_account" in reasons
    # typed values never land in the audit trail
    blob = " ".join((e.detail or {}).__repr__() + (e.entity_id or "") for e in evs)
    assert "no.such.staff.user" not in blob and u.email not in blob


def test_email_is_sent_with_absolute_link_and_uae_expiry(app, db, mail_on):
    u = _make_staff(db, email="noura.lead@clinic.ae")
    r = _ask(fresh(app), u.username, lang="ar")
    assert r.status_code == 200 and result(r.text) == HELP_TEXT["ar"]["s_generic_reset"]
    assert len(mail_on) == 1
    to, subject, body, _kw = mail_on[0]
    assert to == "noura.lead@clinic.ae"
    assert subject == STAFF_RESET_TEXT["ar"]["mail_subject"]
    assert "https://kareshafaa.onrender.com/staff-reset-password?t=" in body
    assert "UAE" in body
    token = _token(mail_on)
    row = db.scalars(select(StaffPasswordReset).where(StaffPasswordReset.user_id == u.id)).one()
    assert row.used_at is None and row.revoked_at is None
    assert row.expires_at - row.created_at == timedelta(minutes=sh.STAFF_RESET_TTL_MINUTES)
    assert token not in (row.token_hash or "")
    assert db.scalar(select(StaffPasswordReset.id).where(StaffPasswordReset.token_hash == token)) is None


def test_token_is_single_use_expires_and_is_replaced(app, db, mail_on):
    u = _make_staff(db)
    _ask(fresh(app), u.username)
    first = _token(mail_on)
    mail_on.clear()
    _ask(fresh(app), u.username)
    second = _token(mail_on)
    assert first != second
    db.expire_all()
    rows = db.scalars(select(StaffPasswordReset).where(StaffPasswordReset.user_id == u.id)
                      .order_by(StaffPasswordReset.id)).all()
    assert len(rows) == 2 and rows[0].revoked_at is not None and rows[1].revoked_at is None
    assert STAFF_RESET_TEXT["en"]["err"]["revoked"] in fresh(app).get(f"/staff-reset-password?t={first}").text
    assert _set(fresh(app), first).status_code == 400
    assert STAFF_RESET_TEXT["en"]["err"]["revoked"] in _set(fresh(app), first).text
    ok = _set(fresh(app), second)
    assert ok.status_code == 200 and STAFF_RESET_TEXT["en"]["done_title"] in ok.text
    again = _set(fresh(app), second)
    assert again.status_code == 400 and STAFF_RESET_TEXT["en"]["err"]["used"] in again.text
    # expire a fresh token
    mail_on.clear()
    # user already reset; request another
    _ask(fresh(app), u.username)
    third = _token(mail_on)
    db.expire_all()
    row = db.scalars(select(StaffPasswordReset).where(StaffPasswordReset.user_id == u.id)
                     .order_by(StaffPasswordReset.id.desc())).first()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    expired = _set(fresh(app), third)
    assert expired.status_code == 400 and STAFF_RESET_TEXT["en"]["err"]["expired"] in expired.text


def test_reset_sets_password_login_works_and_sessions_end(app, db, mail_on):
    u = _make_staff(db)
    s1 = fresh(app)
    assert _login(s1, u.username, PW).status_code == 303
    _ask(fresh(app), u.username)
    r = _set(fresh(app), _token(mail_on))
    assert r.status_code == 200 and u.username in r.text
    assert s1.get("/api/auth/me").status_code == 401
    assert _login(fresh(app), u.username, PW).status_code == 401
    ok = _login(fresh(app), u.username, NEW_PW)
    assert ok.status_code == 303 and "/account/password" not in ok.headers.get("location", "")
    db.expire_all()
    u = db.get(User, u.id)
    assert u.must_change_password is False
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "user.password_reset",
                                             AuditEvent.entity_id == str(u.id)).order_by(AuditEvent.id.desc())).first()
    assert ev is not None and ev.detail.get("method") == "email_link" and ev.detail.get("sessions_ended") is True
    assert ev.detail.get("forced_change") is False and ev.detail.get("notify_sent") is True
    # confirmation email after the change
    assert any("password was changed" in (m[1] or "").lower() or "password was changed" in (m[2] or "").lower()
               or "changed" in (m[1] or "").lower() for m in mail_on)
    assert NEW_PW not in repr(db.scalars(select(AuditEvent.detail).where(AuditEvent.entity_id == str(u.id))).all())


def test_rate_limit_by_ip_and_per_user(app, db, mail_on):
    u = _make_staff(db)
    c = fresh(app)
    for _ in range(sh.HELP_IP_MAX_FAILURES):
        r = _ask(c, "unknown.device.user")
        assert r.status_code == 200 and result(r.text) == GENERIC
    paused = _ask(c, u.username)
    assert paused.status_code == 429 and HELP_TEXT["en"]["s_paused"] in paused.text
    assert all(m[0] != u.email for m in mail_on)  # never reached the known account from this paused device

    # a different device can still request, until the per-user cap
    mail_on.clear()
    for i in range(sh.HELP_RECORD_MAX_FAILURES):
        r = _ask(fresh(app), u.username)
        assert r.status_code == 200 and result(r.text) == GENERIC
    assert len(mail_on) == sh.HELP_RECORD_MAX_FAILURES
    mail_on.clear()
    limited = _ask(fresh(app), u.username)
    assert limited.status_code == 200 and result(limited.text) == GENERIC
    assert mail_on == []
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.staff_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev.detail.get("reason") == "user_limited"


def test_placeholder_and_missing_email_skip_send(app, db, mail_on):
    placeholder = _make_staff(db, email="sara.nasser.1@example.com")
    empty = _make_staff(db, email=None)
    for u in (placeholder, empty):
        r = _ask(fresh(app), u.username)
        assert r.status_code == 200 and result(r.text) == GENERIC
    assert mail_on == []
    reasons = [e.detail.get("reason") for e in db.scalars(
        select(AuditEvent).where(AuditEvent.event == "signin_help.staff_reset_requested")).all()]
    assert "placeholder" in reasons and "no_email" in reasons


def test_mail_failure_is_still_generic_and_audited(app, db, mail_on, monkeypatch):
    u = _make_staff(db)

    def boom(*a, **k):
        raise mail.MailError("server refused")

    monkeypatch.setattr(mail, "send_email", boom)
    r = _ask(fresh(app), u.username)
    assert r.status_code == 200 and result(r.text) == GENERIC
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.staff_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev.outcome == "failure" and ev.detail.get("reason") == "mail_failed"
    assert ev.entity_id == str(u.id)


def test_inactive_and_patient_accounts_do_not_get_mail(app, db, mail_on, pat):
    u = _make_staff(db, active=False, email="gone@clinic.ae")
    r = _ask(fresh(app), u.username)
    p = _ask(fresh(app), pat["user"])
    assert result(r.text) == result(p.text) == GENERIC
    assert mail_on == []
    reasons = [e.detail.get("reason") for e in db.scalars(
        select(AuditEvent).where(AuditEvent.event == "signin_help.staff_reset_requested")).all()]
    assert "inactive" in reasons and "patient_account" in reasons


def test_patient_forgot_password_admin_path_still_opens_a_coordinator_request(app, db, pat):
    before = set(db.scalars(select(StaffPasswordReset.id)))
    r = help_post(fresh(app), "password", via="admin", **_f(pat))
    assert r.status_code == 200 and result(r.text) == HELP_TEXT["en"]["generic_password"]
    assert db.scalar(select(SigninHelpRequest.id).where(SigninHelpRequest.audience == "patient",
                                                        SigninHelpRequest.patient_id == pat["pid"]))
    assert set(db.scalars(select(StaffPasswordReset.id))) == before


def test_admin_request_path_still_works(app, db):
    r = _staff(fresh(app), full_name="Admin Path Only Person", note="Locked out after leave")
    assert r.status_code == 200 and 'data-result="staff_sent"' in r.text
    assert result(r.text) == HELP_TEXT["en"]["s_sent"]
    row = db.scalars(select(SigninHelpRequest).where(SigninHelpRequest.full_name == "Admin Path Only Person")).one()
    assert row.audience == "staff" and row.kind == "password" and row.status == "open"


def test_empty_username_is_a_field_error_not_generic(app):
    r = _ask(fresh(app), "   ")
    assert r.status_code == 400 and HELP_TEXT["en"]["s_err_username"] in r.text
    assert 'data-result="staff_reset_sent"' not in r.text
