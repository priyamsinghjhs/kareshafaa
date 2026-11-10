"""Outbound e-mail from the app mailbox (kareshafaa/comms/mail.py). SMTP is always mocked: no network."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from kareshafaa.comms import mail
from kareshafaa.comms.service import SIM_PREFIX, send_with_ladder
from kareshafaa.config import get_settings
from kareshafaa.models import AuditEvent, Case, Communication, Escalation, StepTask

FAKE_SECRET = "not-a-real-secret-for-tests"


class FakeSMTP:
    sent: list = []
    fail: Exception | None = None
    calls: list = []

    def __init__(self, host, port, timeout=None):
        FakeSMTP.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        FakeSMTP.calls.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.calls.append(("login", user, password == FAKE_SECRET))

    def auth(self, mech, authobject):
        FakeSMTP.calls.append(("auth", mech, authobject()))

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        FakeSMTP.sent.append(msg)


@pytest.fixture()
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.fail = [], [], None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


@pytest.fixture()
def no_mail(monkeypatch):
    s = get_settings()
    for k, v in (("smtp_password", None), ("alert_to", None), ("mail_allow_synthetic", False),
                 ("mail_oauth_client_id", None), ("mail_oauth_refresh_token", None)):
        monkeypatch.setattr(s, k, v)


@pytest.fixture()
def mail_on(monkeypatch, smtp):
    s = get_settings()
    monkeypatch.setattr(s, "smtp_password", FAKE_SECRET)
    monkeypatch.setattr(s, "mail_from", "kareshafaa_com@outlook.com")
    monkeypatch.setattr(s, "smtp_host", None)
    monkeypatch.setattr(s, "alert_to", None)
    monkeypatch.setattr(s, "mail_allow_synthetic", False)
    monkeypatch.setattr(s, "mail_oauth_client_id", None)
    monkeypatch.setattr(s, "mail_oauth_refresh_token", None)
    monkeypatch.setattr(s, "mail_transport", "auto")
    return smtp


def _reset_mailbox():
    """Drop messages sent while setting up a case (queue-owner mail on ingest) so later assertions
    only see the action under test."""
    FakeSMTP.sent, FakeSMTP.calls = [], []


def _case(db, new_case, email="priya.test@realmail.ae", **kw):
    cid = new_case(**kw)["id"]
    db.expire_all()
    c = db.get(Case, cid)
    c.patient.email = email
    db.commit()
    _reset_mailbox()
    return c


def test_defaults_and_not_configured(no_mail, smtp):
    s = get_settings()
    assert s.mail_from == "kareshafaa_com@outlook.com" and s.smtp_port == 587
    assert not mail.configured()
    assert mail.smtp_host() == "smtp-mail.outlook.com"
    st = mail.status()
    assert st["configured"] is False and st["from"] == "kareshafaa_com@outlook.com"
    assert FAKE_SECRET not in str(st)
    with pytest.raises(mail.MailNotConfigured):
        mail.send_email("someone@realmail.ae", "x", "y")
    assert smtp.calls == []


def test_office365_host_for_other_domains(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_from", "alerts@clinic.ae")
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    assert mail.smtp_host() == "smtp.office365.com"


def test_ladder_unchanged_without_password(db, new_case, no_mail, smtp):
    c = _case(db, new_case)
    res = send_with_ladder(db, c, "result_notification")
    assert res["delivered_channel"] != "email" and res["email_sent"] is False
    assert smtp.calls == []
    coms = db.scalars(select(Communication).where(Communication.case_id == c.id)).all()
    assert coms and all(m.simulated and m.channel != "email" for m in coms)
    db.rollback()


def test_real_email_sent_and_stored(db, new_case, mail_on, client, as_user):
    c = _case(db, new_case)
    r = client.post(f"/api/cases/{c.id}/notify", headers=as_user("aisha.rahman"), json={"category": "result_notification"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["delivered_channel"] == "email" and res["email_sent"] is True
    assert len(mail_on.sent) == 1
    msg = mail_on.sent[0]
    assert "kareshafaa_com@outlook.com" in msg["From"] and msg["To"] == "priya.test@realmail.ae"
    assert msg["Subject"].startswith("KareShafaa: ")
    assert ("connect", "smtp-mail.outlook.com", 587, 20.0) in mail_on.calls and ("starttls",) in mail_on.calls
    assert ("login", "kareshafaa_com@outlook.com", True) in mail_on.calls
    db.expire_all()
    com = db.scalar(select(Communication).where(Communication.case_id == c.id, Communication.channel == "email"))
    assert com.simulated is False and com.status == "completed" and not com.content.startswith(SIM_PREFIX)
    assert com.content.split()[0] in msg.get_content()
    ev = db.scalars(select(AuditEvent).where(AuditEvent.case_id == c.id,
                                             AuditEvent.event == "communication.result_notification")).all()[-1]
    assert ev.detail["simulated"] is False and ev.detail["delivered"] == "email"
    assert "priya.test" not in str(ev.detail) and FAKE_SECRET not in str(ev.detail)


def test_smtp_failure_falls_back_to_simulated_ladder(db, new_case, mail_on):
    import smtplib

    mail_on.fail = smtplib.SMTPAuthenticationError(535, b"5.7.3 Authentication unsuccessful kareshafaa_com")
    c = _case(db, new_case, smartphone=True, phone_valid=True)
    res = send_with_ladder(db, c, "result_notification")
    assert res["attempts"][0]["channel"] == "email" and res["attempts"][0]["status"] == "failed"
    assert len(res["attempts"]) > 1 and res["delivered_channel"] != "email"
    com = db.scalar(select(Communication).where(Communication.case_id == c.id, Communication.channel == "email"))
    assert com.status == "failed" and com.simulated is False
    assert FAKE_SECRET not in com.content and "kareshafaa_com" not in com.content
    db.rollback()


@pytest.mark.parametrize("email,mrn", [("someone@example.com", None), ("a@sub.example.org", None),
                                       ("real.person@realmail.ae", "SYN-000123")])
def test_synthetic_never_emailed(db, new_case, mail_on, email, mrn):
    c = _case(db, new_case, email=email)
    if mrn:
        c.patient.mrn = mrn + f"-{c.id}"
    assert not mail.can_send_to_patient(c.patient)
    res = send_with_ladder(db, c, "result_notification")
    assert res["email_sent"] is False and mail_on.sent == [] and mail_on.calls == []
    db.rollback()


def test_synthetic_allowed_when_switch_on(db, new_case, mail_on, monkeypatch):
    c = _case(db, new_case, email="real.person@realmail.ae")
    c.patient.mrn = f"SYN-ALLOW-{c.id}"
    monkeypatch.setattr(get_settings(), "mail_allow_synthetic", True)
    assert mail.can_send_to_patient(c.patient)
    c.patient.email = "x@example.com"
    assert not mail.can_send_to_patient(c.patient)  # reserved example domains: never
    db.rollback()


def test_patient_can_refuse_email(db, new_case, mail_on, client, as_user):
    from kareshafaa.models import Consent, utcnow

    c = _case(db, new_case)
    db.add(Consent(patient_id=c.patient_id, scope="comm_email", decision="deny", recorded_at=utcnow()))
    db.flush()
    res = send_with_ladder(db, c, "result_notification")
    assert res["attempts"][0] == {"channel": "email", "status": "not-done (no consent)"}
    assert mail_on.sent == []
    db.rollback()


def _sweep_to_breach(db, cid):
    from kareshafaa.services.escalation import run_sweep

    step = db.scalar(select(StepTask).where(StepTask.case_id == cid, StepTask.status == "in-progress"))
    sla_h = (step.due_at - step.started_at).total_seconds() / 3600
    return run_sweep(db, now=step.started_at + timedelta(hours=sla_h * 1.1), case_ids=[cid])


def test_escalation_alert_email(db, new_case, mail_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_to", "duty.team@clinic.ae")
    cid = new_case("diabetes_hba1c", 7.5)["id"]
    db.expire_all()
    _reset_mailbox()
    out = _sweep_to_breach(db, cid)
    assert out.get("alert_emails", 0) >= 1
    subjects = [m["Subject"] for m in mail_on.sent]
    assert any(s.startswith(f"KareShafaa: case #{cid} escalation L") for s in subjects)
    assert all(m["To"] == "duty.team@clinic.ae" for m in mail_on.sent)
    body = mail_on.sent[0].get_content()
    assert f"Case #{cid}" in body and FAKE_SECRET not in body
    db.rollback()


def test_escalation_survives_smtp_failure(db, new_case, mail_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_to", "duty.team@clinic.ae")
    mail_on.fail = ConnectionRefusedError("refused")
    cid = new_case("diabetes_hba1c", 7.5)["id"]
    db.expire_all()
    out = _sweep_to_breach(db, cid)
    assert out["supervisor"] + out["reminders"] >= 1 and not out.get("alert_emails")
    assert db.scalars(select(Escalation).where(Escalation.case_id == cid)).all()
    fails = db.scalars(select(AuditEvent).where(AuditEvent.event == "escalation.alert_email_failed",
                                                AuditEvent.case_id == cid)).all()
    assert fails and FAKE_SECRET not in str(fails[0].detail)
    db.rollback()


def test_escalation_alert_skipped_for_synthetic_and_without_alert_to(db, new_case, mail_on, monkeypatch):
    cid = new_case("diabetes_hba1c", 7.5)["id"]
    db.expire_all()
    _reset_mailbox()
    _sweep_to_breach(db, cid)  # alert_to unset
    assert mail_on.sent == []
    db.rollback()
    monkeypatch.setattr(get_settings(), "alert_to", "duty.team@clinic.ae")
    cid = new_case("diabetes_hba1c", 7.5)["id"]
    db.expire_all()
    db.get(Case, cid).patient.mrn = f"SYN-ESC-{cid}"
    _reset_mailbox()
    _sweep_to_breach(db, cid)
    assert mail_on.sent == []
    db.rollback()


def test_admin_test_send(client, as_user, mail_on):
    h = as_user("admin")
    r = client.post("/api/admin/system/mail/test", headers=h, json={"to": "priyam.check@realmail.ae"})
    assert r.status_code == 200, r.text
    assert r.json()["from"] == "kareshafaa_com@outlook.com"
    m = mail_on.sent[-1]
    assert m["Subject"] == "KareShafaa: test email" and m["To"] == "priyam.check@realmail.ae"
    assert "working" in m.get_content()
    assert client.post("/api/admin/system/mail/test", headers=h, json={"to": "not an address"}).status_code == 422
    sysd = client.get("/api/admin/system", headers=h).json()["mail"]
    assert sysd["configured"] is True and sysd["from"] == "kareshafaa_com@outlook.com"
    assert FAKE_SECRET not in client.get("/api/admin/system", headers=h).text
    assert FAKE_SECRET not in client.get("/healthz").text


def test_admin_test_send_failure_is_password_free(client, as_user, mail_on):
    import smtplib

    mail_on.fail = smtplib.SMTPAuthenticationError(535, b"bad credentials for kareshafaa_com@outlook.com")
    r = client.post("/api/admin/system/mail/test", headers=as_user("admin"), json={"to": "priyam.check@realmail.ae"})
    assert r.status_code == 502 and "sign-in" in r.json()["detail"]
    assert FAKE_SECRET not in r.text and "bad credentials" not in r.text


def test_admin_test_send_not_configured_and_rbac(client, as_user, no_mail, smtp):
    r = client.post("/api/admin/system/mail/test", headers=as_user("admin"), json={"to": "priyam.check@realmail.ae"})
    assert r.status_code == 503 and "KARESHAFAA_SMTP_PASSWORD" in r.json()["detail"]
    assert client.post("/api/admin/system/mail/test", headers=as_user("aisha.rahman"),
                       json={"to": "priyam.check@realmail.ae"}).status_code == 403
    assert client.get("/healthz").json()["mail"] == {"configured": False, "from": "kareshafaa_com@outlook.com"}
    assert smtp.calls == []


@pytest.fixture()
def oauth(monkeypatch, smtp, no_mail):
    s = get_settings()
    monkeypatch.setattr(s, "mail_oauth_client_id", "00000000-test-client")
    monkeypatch.setattr(s, "mail_oauth_refresh_token", "rt-not-real")
    monkeypatch.setattr(s, "mail_transport", "auto")
    monkeypatch.setattr(mail, "_TOKEN", {})
    seen = {"token": [], "graph": []}

    def fake_post(url, data, timeout):
        seen["token"].append((url, data["grant_type"], data["refresh_token"], data["scope"]))
        return {"access_token": "at-not-real", "expires_in": 3600, "refresh_token": "rt-rotated"}

    def fake_json(url, payload, token, timeout):
        seen["graph"].append((url, payload, token))
        return 202

    monkeypatch.setattr(mail, "_http_post_form", fake_post)
    monkeypatch.setattr(mail, "_http_post_json", fake_json)
    return seen


def test_oauth2_graph_is_default(oauth, smtp):
    assert mail.configured() and mail.auth_method() == "oauth2" and mail.transport() == "graph"
    mail.send_email("priyam.check@realmail.ae", "KareShafaa: test email", "hello")
    mail.send_email("priyam.check@realmail.ae", "KareShafaa: test email", "again")
    assert len(oauth["token"]) == 1  # access token cached
    url, grant, rt, scope = oauth["token"][0]
    assert url.endswith("/consumers/oauth2/v2.0/token") and grant == "refresh_token" and "Mail.Send" in scope
    gurl, payload, tok = oauth["graph"][0]
    assert gurl == "https://graph.microsoft.com/v1.0/me/sendMail" and tok == "at-not-real"
    m = payload["message"]
    assert m["subject"] == "KareShafaa: test email" and m["toRecipients"][0]["emailAddress"]["address"] == "priyam.check@realmail.ae"
    assert m["body"]["content"].strip() == "hello" and payload["saveToSentItems"] is True
    assert smtp.calls == []  # no SMTP port used (blocked on Render's free plan)
    st = mail.status()
    assert st["transport"] == "graph" and "rt-" not in str(st) and "at-not" not in str(st)


def test_oauth2_smtp_xoauth2(oauth, smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_transport", "smtp")
    mail.send_email("priyam.check@realmail.ae", "KareShafaa: test email", "hello")
    assert "SMTP.Send" in oauth["token"][0][3] and oauth["graph"] == []
    auth = [c for c in smtp.calls if c[0] == "auth"]
    assert auth[0][1] == "XOAUTH2" and auth[0][2] == "user=kareshafaa_com@outlook.com\x01auth=Bearer at-not-real\x01\x01"
    assert not [c for c in smtp.calls if c[0] == "login"] and len(smtp.sent) == 1


def test_graph_errors_are_secret_free(oauth, monkeypatch):
    def deny(url, payload, token, timeout):
        raise mail._GraphError(403, "ErrorAccessDenied")

    monkeypatch.setattr(mail, "_http_post_json", deny)
    with pytest.raises(mail.MailError) as ei:
        mail.send_email("priyam.check@realmail.ae", "x", "y")
    assert "Mail.Send" in str(ei.value) and "at-not-real" not in str(ei.value)


def test_oauth2_expired_refresh_token_message(monkeypatch, oauth):

    def fake_post(url, data, timeout):
        raise mail._OAuthError("invalid_grant")

    monkeypatch.setattr(mail, "_http_post_form", fake_post)
    with pytest.raises(mail.MailError) as ei:
        mail.send_email("priyam.check@realmail.ae", "x", "y")
    assert "outlook_oauth_setup.py" in str(ei.value) and "rt-not-real" not in str(ei.value)
    assert oauth["graph"] == []


def test_outlook_password_refusal_explains_oauth(monkeypatch, mail_on):
    import smtplib

    mail_on.fail = smtplib.SMTPAuthenticationError(535, b"5.7.139 Authentication unsuccessful, basic authentication is disabled")
    with pytest.raises(mail.MailError) as ei:
        mail.send_email("priyam.check@realmail.ae", "x", "y")
    assert "KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN" in str(ei.value) and "basic authentication" not in str(ei.value)



# ------------------------------------------------------------------------------------------------ test inboxes + queue-owner mail
from kareshafaa.synth.test_mailboxes import PATIENT_MAILBOXES, STAFF_MAILBOXES

TEST_PATIENT_ADDR = PATIENT_MAILBOXES["patient.en"]


def test_allowlisted_syn_patient_can_be_emailed(db, new_case, mail_on):
    c = _case(db, new_case, email=TEST_PATIENT_ADDR)
    c.patient.mrn = f"SYN-EN-{c.id}"
    assert mail.can_send_to_patient(c.patient)
    res = send_with_ladder(db, c, "result_notification")
    assert res["email_sent"] is True and len(mail_on.sent) == 1
    assert mail_on.sent[0]["To"] == TEST_PATIENT_ADDR
    db.rollback()


def test_allowlisted_does_not_override_example_domain(db, new_case, mail_on):
    c = _case(db, new_case, email="someone@example.com")
    c.patient.mrn = f"SYN-EN-{c.id}"
    assert TEST_PATIENT_ADDR not in (c.patient.email or "")
    assert not mail.can_send_to_patient(c.patient)
    db.rollback()


def test_seeded_test_mailboxes(db):
    from kareshafaa.models import Patient, User

    for uname, addr in STAFF_MAILBOXES.items():
        u = db.scalar(select(User).where(User.username == uname))
        assert u is not None and u.email == addr, uname
    u = db.scalar(select(User).where(User.username == "patient.en"))
    assert u is not None and u.patient_id
    p = db.get(Patient, u.patient_id)
    assert p.email == TEST_PATIENT_ADDR and p.mrn.upper().startswith("SYN-")
    other = db.scalar(select(User).where(User.username == "patient.ar"))
    if other and other.patient_id:
        assert db.get(Patient, other.patient_id).email != TEST_PATIENT_ADDR


def test_public_user_includes_email(client, as_user):
    from kareshafaa.accounts import public_user
    from kareshafaa.models import User
    from kareshafaa.db import session_factory

    s = session_factory()()
    try:
        u = s.scalar(select(User).where(User.username == "admin"))
        pub = public_user(u)
        assert pub["email"] == STAFF_MAILBOXES["admin"]
        assert "password_hash" not in pub
    finally:
        s.close()
    items = client.get("/api/admin/users", headers=as_user("admin")).json()["items"]
    admin = next(x for x in items if x["username"] == "admin")
    assert admin["email"] == STAFF_MAILBOXES["admin"]


def test_admin_can_set_and_clear_staff_email(client, as_user):
    h = as_user("admin")
    name = "mail.tester." + str(id(h))[-6:]
    r = client.post("/api/admin/users", headers=h,
                    json={"username": name, "display_name": "Mail Tester", "role": "analyst",
                          "email": "  Staff.Check@Realmail.ae "})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "staff.check@realmail.ae"
    uid = r.json()["user"]["id"]
    bad = client.post(f"/api/admin/users/{uid}/role", headers=h, json={"role": "analyst", "email": "not-an-address"})
    assert bad.status_code in (400, 422)
    r = client.post(f"/api/admin/users/{uid}/role", headers=h, json={"role": "analyst", "email": ""})
    assert r.status_code == 200 and r.json()["user"]["email"] is None


def _actor(db, username):
    from kareshafaa.models import User
    from kareshafaa.synth.history import actor_from_user
    return actor_from_user(db.scalar(select(User).where(User.username == username)))


def _owner_user(db, username):
    from kareshafaa.models import User
    return db.scalar(select(User).where(User.username == username))


def test_assign_sample_syn_does_not_call_send_email(db, new_case, mail_on):
    from kareshafaa.services import workflow as wf
    c = _case(db, new_case, email="sample@example.com")
    c.patient.mrn = f"SYN-SAMPLE-{c.id}"
    dest = _owner_user(db, "omar.haddad")
    wf.assign(db, c, dest.practitioner_id, _actor(db, "aisha.rahman"), "reassign for coverage")
    assert mail_on.sent == []
    db.rollback()


def test_assign_allowlisted_patient_emails_owner_once(db, new_case, mail_on, monkeypatch):
    from kareshafaa.services import workflow as wf
    monkeypatch.setattr(get_settings(), "public_base_url", "https://kareshafaa.example")
    c = _case(db, new_case, email=TEST_PATIENT_ADDR)
    c.patient.mrn = f"SYN-EN-{c.id}"
    dest = _owner_user(db, "omar.haddad")
    assert dest.email == STAFF_MAILBOXES["omar.haddad"]
    wf.assign(db, c, dest.practitioner_id, _actor(db, "aisha.rahman"), "reassign to the GP")
    assert len(mail_on.sent) == 1
    msg = mail_on.sent[0]
    assert msg["To"] == STAFF_MAILBOXES["omar.haddad"]
    assert msg["Subject"] == "KareShafaa: a case was added to your queue"
    body = msg.get_content()
    assert f"Case: #{c.id}" in body and "assigned to you" in body
    assert c.patient.given_name in body and f"https://kareshafaa.example/cases/{c.id}" in body
    assert "emirates" not in body.lower()
    if c.patient.phone:
        assert c.patient.phone not in body
    db.rollback()


def test_assign_to_pool_does_not_send(db, new_case, mail_on):
    from kareshafaa.models import Practitioner
    from kareshafaa.services import workflow as wf
    c = _case(db, new_case, email=TEST_PATIENT_ADDR)
    c.patient.mrn = f"SYN-EN-{c.id}"
    pool = db.scalar(select(Practitioner).where(Practitioner.is_pool.is_(True),
                                                Practitioner.programme == c.programme))
    assert pool is not None
    wf.assign(db, c, pool.id, _actor(db, "aisha.rahman"), "move to the team pool")
    assert mail_on.sent == []
    db.rollback()


def test_assign_mail_error_does_not_raise(db, new_case, mail_on, monkeypatch):
    from kareshafaa.services import workflow as wf
    monkeypatch.setattr(mail, "send_email", lambda *a, **k: (_ for _ in ()).throw(mail.MailError("server refused")))
    c = _case(db, new_case, email=TEST_PATIENT_ADDR)
    c.patient.mrn = f"SYN-EN-{c.id}"
    dest = _owner_user(db, "omar.haddad")
    out = wf.assign(db, c, dest.practitioner_id, _actor(db, "aisha.rahman"), "reassign to the GP")
    assert out.owner_id == dest.practitioner_id
    db.rollback()
