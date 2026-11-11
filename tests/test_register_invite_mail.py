"""Registration automatically emails a patient-app sign-up link (choose username + password)."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest

from kareshafaa.comms import mail
from kareshafaa.config import get_settings
from kareshafaa.models import AuditEvent, Communication, Consent, Patient, PatientInvite, User, utcnow
from kareshafaa.security import Actor
from kareshafaa.services import invites
from tests.test_patient_invites import _browser, _csrf
from tests.test_patient_registration import person

FAKE_SECRET = "not-a-real-secret-for-tests"
PW = "Lantern-Orbit-Cobalt-77"


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


def _reg(client, h, **kw):
    body = person(email="noura.signup@clinic.ae", language="en", **kw)
    return client.post("/api/patients", headers=h, json=body), body


def _token(sent) -> str:
    return re.search(r"/activate\?t=([A-Za-z0-9_-]+)", sent[0][2]).group(1)


def _claim(browser, token, *, birth="1974-02-11", username="noura.clinic", password=PW, confirm=None, lang="en"):
    tok, _ = _csrf(browser, "/activate?t=" + token)
    return browser.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": birth,
                                           "username": username, "password": password,
                                           "confirm": password if confirm is None else confirm, "lang": lang})


def test_registration_emails_signup_link_when_patient_has_email(client, as_user, db, mail_on):
    h = as_user("aisha.rahman")
    r, _body = _reg(client, h, birth_date="1974-02-11")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["signup_email"] == "sent"
    assert out["signup_email_masked"].startswith("n") and "@clinic.ae" in out["signup_email_masked"]
    assert "noura.signup" not in out["signup_email_masked"]
    pid = out["patient_id"]
    assert len(mail_on) == 1
    to, subject, msg, _kw = mail_on[0]
    assert to == "noura.signup@clinic.ae"
    assert "set up your patient app account" in subject
    assert "https://kareshafaa.onrender.com/activate?t=" in msg
    token = _token(mail_on)
    com = db.query(Communication).filter_by(patient_id=pid, category="account_invite", channel="email").one()
    assert com.status == "completed" and com.simulated is False
    assert token not in com.content and "?t=" not in com.content
    row = db.query(PatientInvite).filter_by(patient_id=pid).one()
    assert row.channel == "email" and row.used_at is None and row.revoked_at is None
    assert row.expires_at - row.created_at == timedelta(hours=72)
    ev = db.query(AuditEvent).filter_by(event="patient.account_invited", entity_id=str(pid)).one()
    assert ev.detail["email_sent"] is True and ev.detail["channel"] == "email"
    rec = client.get(f"/api/patients/{pid}", headers=h).json()["account"]
    assert rec["state"] == "invited" and rec["invite"]["channel"] == "email"


def test_registration_without_email_does_not_invite(client, as_user, db, mail_on):
    h = as_user("aisha.rahman")
    r = client.post("/api/patients", headers=h, json=person(email=None, preferred_channel="sms"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["signup_email"] == "no_email" and out["signup_email_masked"] is None
    assert mail_on == []
    assert db.query(PatientInvite).filter_by(patient_id=out["patient_id"]).count() == 0
    rec = client.get(f"/api/patients/{out['patient_id']}", headers=h).json()["account"]
    assert rec["state"] == "none"


def test_registration_survives_mailer_failure_and_keeps_invite(client, as_user, db, mail_on, monkeypatch):
    monkeypatch.setattr(mail, "send_email", lambda *a, **k: (_ for _ in ()).throw(mail.MailError("server refused")))
    h = as_user("aisha.rahman")
    r, _body = _reg(client, h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["signup_email"] == "failed" and db.get(Patient, out["patient_id"]) is not None
    pid = out["patient_id"]
    row = db.query(PatientInvite).filter_by(patient_id=pid).one()
    assert row.used_at is None and row.revoked_at is None
    com = db.query(Communication).filter_by(patient_id=pid, category="account_invite", channel="email").one()
    assert com.status == "failed" and com.simulated is False and "server refused" in com.content
    ev = db.query(AuditEvent).filter_by(event="patient.account_invited", entity_id=str(pid)).one()
    assert ev.detail["email_sent"] is False


def test_claim_sets_chosen_username_and_password_then_login(app, client, as_user, db, mail_on):
    h = as_user("aisha.rahman")
    r, _body = _reg(client, h, birth_date="1974-02-11")
    pid = r.json()["patient_id"]
    token = _token(mail_on)
    b = _browser(app, "10.30.0.1")
    tok, page = _csrf(b, "/activate?t=" + token)
    assert 'name="username"' in page and "Choose a username" in page
    r = _claim(b, token)
    assert r.status_code == 200, r.text[:400]
    assert "Your account is ready" in r.text and "noura.clinic" in r.text
    u = db.query(User).filter_by(patient_id=pid, role="patient").one()
    assert u.username == "noura.clinic" and u.password_hash
    b2 = _browser(app, "10.30.0.2")
    r = _claim(b2, token, username="other.name", password="Another-Lantern-Path-55")
    assert r.status_code == 400 and "already been used" in r.text
    tok, _ = _csrf(b2, "/login")
    r = b2.post("/login", data={"csrf_token": tok, "username": "noura.clinic", "password": PW})
    assert r.status_code == 303 and r.headers["location"].startswith("/patient")


def test_claim_rejects_taken_and_invalid_username(app, client, as_user, db, mail_on):
    h = as_user("aisha.rahman")
    r, _ = _reg(client, h, birth_date="1974-02-11")
    pid = r.json()["patient_id"]
    token = _token(mail_on)
    b = _browser(app, "10.30.0.3")
    r = _claim(b, token, username="aisha.rahman")
    assert r.status_code == 422 and "already taken" in r.text
    r = _claim(b, token, username="No Spaces!")
    assert r.status_code == 422 and "3–80" in r.text
    db.expire_all()
    assert db.query(PatientInvite).filter_by(patient_id=pid).one().used_at is None
    r = _claim(b, token, username="noura.ok")
    assert r.status_code == 200 and "noura.ok" in r.text


def test_expired_email_link_is_refused(app, client, as_user, db, mail_on):
    h = as_user("aisha.rahman")
    r, _ = _reg(client, h, birth_date="1974-02-11")
    pid = r.json()["patient_id"]
    token = _token(mail_on)
    db.expire_all()
    row = db.query(PatientInvite).filter_by(patient_id=pid).one()
    row.expires_at = row.created_at - timedelta(minutes=1)
    db.commit()
    b = _browser(app, "10.30.0.4")
    r = _claim(b, token, username="too.late")
    assert r.status_code == 400 and "expired" in r.text


def test_resend_replaces_token_and_old_email_link_stops_working(app, client, as_user, db, mail_on):
    h = as_user("aisha.rahman")
    r, _ = _reg(client, h, birth_date="1974-02-11")
    pid = r.json()["patient_id"]
    old_token = _token(mail_on)
    r2 = client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "email"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["email_sent"] is True and r2.json()["channel"] == "email"
    new_token = r2.json()["link"].split("t=")[1]
    assert new_token != old_token and len(mail_on) == 2
    db.expire_all()
    rows = db.query(PatientInvite).filter_by(patient_id=pid).order_by(PatientInvite.id).all()
    assert len(rows) == 2 and rows[0].revoked_at and rows[1].revoked_at is None
    b = _browser(app, "10.30.0.5")
    r = _claim(b, old_token, username="old.link")
    assert r.status_code == 400
    r = _claim(b, new_token, username="new.link")
    assert r.status_code == 200 and "new.link" in r.text


def test_refused_email_consent_skips_auto_send(client, as_user, db, mail_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_password", None)
    h = as_user("aisha.rahman")
    r = client.post("/api/patients", headers=h, json=person(email="refused@clinic.ae", language="en"))
    assert r.status_code == 200, r.text
    assert r.json()["signup_email"] == "not_sent"
    pid = r.json()["patient_id"]
    db.add(Consent(patient_id=pid, scope="comm_email", decision="deny", recorded_at=utcnow(),
                   note="Patient asked not to be emailed"))
    db.commit()
    monkeypatch.setattr(get_settings(), "smtp_password", FAKE_SECRET)
    mail_on.clear()
    staff = db.query(User).filter_by(username="aisha.rahman").one()
    actor = Actor(user_id=staff.id, role="coordinator", display=staff.display_name, is_human=True,
                  practitioner_id=staff.practitioner_id, org_id=staff.practitioner.org_id if staff.practitioner else None)
    out = invites.invite_after_registration(db, actor, db.get(Patient, pid),
                                            base_url="https://kareshafaa.onrender.com")
    db.commit()
    assert out["signup_email"] == "refused" and mail_on == []
    r = client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "email"})
    assert r.status_code == 422 and "email" in r.text.lower()


def test_example_dot_com_and_unconfigured_mail_are_not_sent(client, as_user, db, mail_on, monkeypatch):
    h = as_user("aisha.rahman")
    r = client.post("/api/patients", headers=h, json=person(email="demo@example.com"))
    assert r.status_code == 200, r.text
    assert r.json()["signup_email"] == "not_sent"
    assert mail_on == []
    assert db.query(PatientInvite).filter_by(patient_id=r.json()["patient_id"]).count() == 0
    monkeypatch.setattr(get_settings(), "smtp_password", None)
    r = client.post("/api/patients", headers=h, json=person(email="real.person@clinic.ae"))
    assert r.status_code == 200, r.text
    assert r.json()["signup_email"] == "not_sent"
    assert mail_on == []
    assert db.query(PatientInvite).filter_by(patient_id=r.json()["patient_id"]).count() == 0


def test_gp_registration_also_sends_signup_email(client, as_user, db, mail_on):
    h = as_user("omar.haddad")
    staff = db.query(User).filter_by(username="omar.haddad").one()
    org_id = staff.practitioner.org_id if staff.practitioner else 3
    r, _ = _reg(client, h, managing_org_id=org_id, gp_id=staff.practitioner_id)
    assert r.status_code == 200, r.text
    assert r.json()["signup_email"] == "sent"
    assert len(mail_on) == 1
