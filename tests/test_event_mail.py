"""Rich appointment and result emails: content, triggers, gating, idempotency, clinical safety."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from kareshafaa.comms import mail
from kareshafaa.comms.event_mail import (APPT_CATEGORIES, RESULT_CATEGORIES, format_uae, render,
                                         render_appointment, render_result)
from kareshafaa.comms.event_text import ALARM_WORDS, TEXT
from kareshafaa.comms.service import safe_send_with_ladder, send_with_ladder
from kareshafaa.config import get_settings
from kareshafaa.models import Appointment, Case, Communication, Consent, DiagnosticReport, Patient, utcnow

FAKE_SECRET = "not-a-real-secret-for-tests"
REAL = "priya.events@realmail.ae"


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
    monkeypatch.setattr(s, "public_base_url", "https://kareshafaa.onrender.com")
    return smtp


def _reset():
    FakeSMTP.sent, FakeSMTP.calls = [], []


def _plain(msg=None):
    if msg is None:
        assert FakeSMTP.sent, "no e-mail was sent"
        msg = FakeSMTP.sent[-1]
    return mail.plain_content(msg)


def _html(msg=None):
    if msg is None:
        msg = FakeSMTP.sent[-1]
    return mail.html_content(msg) or ""


def _give_email(db, case_or_patient, addr=REAL):
    p = case_or_patient if isinstance(case_or_patient, Patient) else case_or_patient.patient
    p.email = addr
    db.commit()
    _reset()
    return p


# ------------------------------------------------------------------------------------------------ content (no DB)


def _appt_ctx(lang="en", **kw):
    from types import SimpleNamespace

    patient = SimpleNamespace(given_name="Emma", family_name="Clarke", language=lang)
    clin = SimpleNamespace(name="Dr. Anjali Menon", role="specialist", specialty="gastroenterology", is_pool=False)
    ctx = dict(category="appointment_confirmation", patient=patient, programme="colorectal_fit", case_id=1395,
               request_id=None, appointment_id=10, start=datetime(2026, 11, 15, 9, 0, 0), previous_start=None,
               clinician=clin, place="Cleveland Clinic Abu Dhabi", address="Abu Dhabi", phone=None,
               service="Colonoscopy", department=None, bring=None, case=None)
    ctx.update(kw)
    return ctx


def _result_ctx(tier="high", conclusion="actionable", value=180.0, lang="en", **kw):
    from types import SimpleNamespace

    patient = SimpleNamespace(given_name="Emma", family_name="Clarke", language=lang)
    obs = SimpleNamespace(value=value, value_text=None, unit="ug/g",
                          display="Faecal immunochemical test (FIT) haemoglobin",
                          interpretation="N" if conclusion == "negative" else "H")
    rep = SimpleNamespace(id=5, display=obs.display, conclusion=conclusion,
                          issued_at=datetime(2026, 10, 2, 8, 0, 0),
                          specimen_collected_at=datetime(2026, 10, 1, 6, 0, 0),
                          ordering_clinician="Dr. Omar Haddad", observations=[obs])
    case = None
    if conclusion != "negative":
        case = SimpleNamespace(id=42, programme="colorectal_fit", tier=tier, patient=patient,
                               needs_confirmation=True, pathway=None, report=rep, rule_id="FIT-POS")
    ctx = dict(category="result_notification", patient=patient, programme="colorectal_fit",
               case=case, case_id=getattr(case, "id", None), report=rep, report_id=5, obs=obs,
               lab="Central Screening Laboratory", ordered_by="Dr. Omar Haddad",
               collected_at=rep.specimen_collected_at, issued_at=rep.issued_at,
               owner=SimpleNamespace(name="Aisha Rahman", role="coordinator", specialty=None, is_pool=False),
               conclusion=conclusion)
    ctx.update(kw)
    return ctx


@pytest.mark.parametrize("lang", ["en", "ar", "hi", "ur"])
def test_texts_same_keys_in_every_language(lang):
    def keys(d, p=""):
        out = set()
        for k, v in d.items():
            out |= keys(v, f"{p}{k}.") if isinstance(v, dict) else {p + k}
        return out

    assert keys(TEXT[lang]) == keys(TEXT["en"])


def test_uae_time_is_asia_dubai_and_labelled():
    # 09:00 UTC on 15 Nov 2026 is 13:00 in Asia/Dubai (UTC+4, no DST).
    s = format_uae(datetime(2026, 11, 15, 9, 0, 0), "en")
    assert s == "Sunday, 15 November 2026 at 13:00 UAE time (Asia/Dubai)"
    ar = format_uae(datetime(2026, 11, 15, 9, 0, 0), "ar")
    assert "13:00" in ar and "15" in ar and "نوفمبر" in ar and "آسيا/دبي" in ar


@pytest.mark.parametrize("lang", ["en", "ar", "hi", "ur"])
def test_appointment_email_has_the_important_details(lang):
    sub, plain, html = render_appointment(lang, _appt_ctx(lang))
    assert sub.startswith("KareShafaa:")
    for needle in ("13:00", "Dr. Anjali Menon", "Cleveland Clinic Abu Dhabi", "1395", "Colonoscopy"):
        assert needle in plain and needle in html
    assert "demo" not in (plain + html + sub).lower() and "prototype" not in (plain + html + sub).lower()
    expected_dir = "rtl" if lang in ("ar", "ur") else "ltr"
    assert f'dir="{expected_dir}"' in html
    assert "KareShafaa" in html
    assert TEXT[lang]["labels"]["when"] in plain
    assert TEXT[lang]["change_how"] in plain
    assert TEXT[lang]["bring_default"] in plain


@pytest.mark.parametrize("lang", ["en", "ar", "hi", "ur"])
def test_result_email_has_test_details_and_safe_wording(lang):
    sub, plain, html = render_result(lang, _result_ctx(lang=lang))
    assert sub.startswith("KareShafaa:")
    assert "180" in plain and "ug/g" in plain
    assert "80" in plain  # usual range
    assert "42" in plain
    assert "Dr. Omar Haddad" in plain
    assert "Central Screening Laboratory" in plain
    assert "demo" not in (plain + html).lower() and "prototype" not in (plain + html).lower()
    # The flag and the lead do not use alarming words (the signed-off programme meaning may
    # reassure that a finding is 'usually NOT cancer', which is allowed in the explanation).
    lead = TEXT[lang]["lead"]["actionable"]
    flag = TEXT[lang]["flag"]["actionable"]
    residual = (lead + " " + flag)
    for w in ("critical", "urgent", "abnormal", "emergency", "alarm"):
        assert w not in residual.lower()
    assert lead in plain
    assert f'dir="{"rtl" if lang in ("ar", "ur") else "ltr"}"' in html


def test_critical_result_leads_with_care_team_will_contact_you():
    sub, plain, html = render_result("en", _result_ctx(tier="critical"))
    assert plain.startswith("Hello Emma,")
    # The first paragraph after the greeting is the lead, before the numeric result.
    lead_at = plain.index("Your care team will contact you about this result")
    value_at = plain.index("180")
    assert lead_at < value_at
    assert "do not need to go to an emergency department unless you feel unwell" in plain.lower()
    assert "critical" not in plain.split("Your bowel screening")[0].lower()  # not in the lead/flag area as a label


def test_negative_result_does_not_use_actionable_programme_wording():
    _, plain, _ = render_result("en", _result_ctx(conclusion="negative", value=6.0, case=None, case_id=None))
    assert "Within the usual range" in plain
    assert "blood in your stool" not in plain.lower()
    assert "No follow-up is needed from this test" in plain


def test_rescheduled_email_shows_the_previous_time():
    prev = datetime(2026, 11, 15, 9, 0, 0)
    new = datetime(2026, 11, 20, 10, 0, 0)
    _, plain, _ = render_appointment("en", _appt_ctx(category="appointment_changed", start=new, previous_start=prev))
    assert "20 November 2026 at 14:00" in plain  # 10:00 UTC
    assert "15 November 2026 at 13:00" in plain
    assert "moved" in plain.lower() or "new" in plain.lower()


def test_omitted_optional_fields_do_not_leave_blank_rows():
    ctx = _appt_ctx(clinician=None, place="Al Ain Clinic", address=None, phone=None, service=None)
    _, plain, html = render_appointment("en", ctx)
    assert "Al Ain Clinic" in plain
    assert "Clinician:" not in plain
    assert "Phone:" not in plain
    assert "Address:" not in plain or "Address:" in plain and "Abu Dhabi" not in plain
    # address was None, so the Address row is omitted
    assert "Address:" not in plain


# ------------------------------------------------------------------------------------------------ triggers + gating


def _to_accepted(client, as_user, db, new_case, email=REAL):
    """Staff path: notify (by e-mail when mail is on) -> teach-back -> consult -> referral accepted."""
    from tests.test_journey_api import CORRECT, SUMMARY

    cid = new_case(value=250)["id"]
    db.expire_all()
    c = db.get(Case, cid)
    c.patient.email = email
    db.commit()
    _reset()
    co, gp, sp = (as_user(u) for u in ("aisha.rahman", "omar.haddad", "vikram.mehta"))
    r = client.post(f"/api/cases/{cid}/notify", headers=co, json={"category": "result_notification"})
    assert r.status_code == 200, r.text
    # Teach-back is available once the patient has been notified.
    db.expire_all()
    if db.get(Case, cid).state == "triaged":
        # e-mail was not delivered (mailbox off): record the call the same way notify_and_ack does
        client.post(f"/api/cases/{cid}/call", headers=co, json={"outcome": "reached", "note": "explained result"})
    r = client.post(f"/api/cases/{cid}/teach-back", headers=co,
                    json={"answers": CORRECT, "confidence": 4, "language": "en"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/cases/{cid}/transition", headers=gp,
                       json={"to_state": "consultation", "note": "GP reviewed"}).status_code == 200
    assert client.post(f"/api/cases/{cid}/referral", headers=gp,
                       json={"performer_org_id": 9, "performer_id": 31, "summary": SUMMARY}).status_code == 200
    assert client.post(f"/api/cases/{cid}/referral/decision", headers=sp,
                       json={"accept": True, "note": "ok"}).status_code == 200
    _reset()
    return cid, co, gp, sp


def test_staff_booking_emails_appointment_details(db, new_case, mail_on, client, as_user):
    cid, co, *_ = _to_accepted(client, as_user, db, new_case)
    start = datetime(2026, 11, 15, 9, 0, 0)  # 13:00 UAE
    r = client.post(f"/api/cases/{cid}/appointment", headers=co, json={"start": start.isoformat() + "Z"})
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "appointment_booked"
    assert len(FakeSMTP.sent) == 1
    msg, body, html = FakeSMTP.sent[0], _plain(), _html()
    assert msg["Subject"].startswith("KareShafaa:")
    assert "13:00" in body and "UAE time (Asia/Dubai)" in body
    assert f"Case #{cid}" in body
    assert "https://kareshafaa.onrender.com/patient" in body
    assert "https://kareshafaa.onrender.com/patient" in html
    assert mail.html_content(msg)
    assert "KareShafaa" in html
    db.expire_all()
    com = db.scalar(select(Communication).where(Communication.case_id == cid, Communication.channel == "email",
                                                Communication.category == "appointment_confirmation",
                                                Communication.status == "completed"))
    assert com is not None and com.simulated is False
    assert f"appt:" in (com.template_key or "")


def test_reschedule_and_cancel_send_their_own_emails(db, new_case, mail_on, client, as_user):
    cid, co, *_ = _to_accepted(client, as_user, db, new_case)
    start = utcnow() + timedelta(days=3)
    assert client.post(f"/api/cases/{cid}/appointment", headers=co, json={"start": start.isoformat()}).status_code == 200
    _reset()
    new_start = start + timedelta(days=2)
    r = client.post(f"/api/cases/{cid}/appointment/reschedule", headers=co,
                    json={"start": new_start.isoformat(), "reason_code": "patient_request",
                          "barrier_code": "transport", "note": "taxi not available"})
    assert r.status_code == 200, r.text
    assert len(FakeSMTP.sent) == 1
    assert "appointment_changed" in db.scalar(select(Communication.category).where(
        Communication.case_id == cid, Communication.channel == "email",
        Communication.category == "appointment_changed"))
    _reset()
    r = client.post(f"/api/cases/{cid}/appointment/cancel", headers=co,
                    json={"reason_code": "clinic_cancelled", "note": "Endoscopist on leave"})
    assert r.status_code == 200, r.text
    assert len(FakeSMTP.sent) == 1
    assert "cancelled" in _plain().lower() or "إلغاء" in _plain() or "रद्द" in _plain()


def test_booking_survives_a_mail_server_refusal(db, new_case, mail_on, client, as_user):
    import smtplib

    cid, co, *_ = _to_accepted(client, as_user, db, new_case)
    mail_on.fail = smtplib.SMTPAuthenticationError(535, b"5.7.3 Authentication unsuccessful")
    start = utcnow() + timedelta(days=4)
    r = client.post(f"/api/cases/{cid}/appointment", headers=co, json={"start": start.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "appointment_booked"
    db.expire_all()
    appt = db.scalar(select(Appointment).where(Appointment.case_id == cid, Appointment.status == "booked"))
    assert appt is not None
    # the e-mail row is stored as failed; the booking is kept
    failed = db.scalar(select(Communication).where(Communication.case_id == cid, Communication.channel == "email",
                                                   Communication.category == "appointment_confirmation"))
    assert failed is not None and failed.status == "failed" and FAKE_SECRET not in (failed.content or "")


def test_result_notify_emails_the_released_result(db, new_case, mail_on, client, as_user):
    cid = new_case(value=250)["id"]
    db.expire_all()
    c = db.get(Case, cid)
    _give_email(db, c)
    r = client.post(f"/api/cases/{cid}/notify", headers=as_user("aisha.rahman"),
                    json={"category": "result_notification"})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["email_sent"] is True
    body = _plain()
    assert "250" in body and "ug/g" in body
    assert "80" in body
    assert f"Case #{cid}" in body
    assert "Your care team will go through this result with you" in body
    assert "https://kareshafaa.onrender.com/patient" in body
    # second notify of the same report does not send a second e-mail
    _reset()
    r2 = client.post(f"/api/cases/{cid}/notify", headers=as_user("aisha.rahman"),
                     json={"category": "result_notification"})
    assert r2.status_code == 200, r2.text
    assert FakeSMTP.sent == []
    assert r2.json()["result"]["attempts"][0].get("already_sent") is True


def test_actionable_ingest_does_not_email_until_released(db, make_patient, mail_on, client, as_user):
    pid = make_patient()
    db.expire_all()
    db.get(Patient, pid).email = REAL
    db.commit()
    _reset()
    r = client.post("/api/results", headers=as_user("aisha.rahman"),
                    json={"patient_id": pid, "programme": "colorectal_fit", "value": 250, "unit": "ug/g"})
    assert r.status_code == 200 and r.json()["actionable"], r.text
    # A queue-owner staff e-mail may go out (existing behaviour); the patient is not told yet.
    assert not any(m["To"] == REAL for m in FakeSMTP.sent)


def test_negative_result_filed_by_staff_is_emailed(db, make_patient, mail_on, client, as_user):
    pid = make_patient()
    db.expire_all()
    db.get(Patient, pid).email = REAL
    db.commit()
    _reset()
    r = client.post("/api/results", headers=as_user("aisha.rahman"),
                    json={"patient_id": pid, "programme": "colorectal_fit", "value": 10, "unit": "ug/g"})
    assert r.status_code == 200, r.text
    assert not r.json().get("actionable")
    assert len(FakeSMTP.sent) == 1
    body = _plain()
    assert "10" in body and "ug/g" in body
    assert "blood in your stool" not in body.lower()
    assert "usual range" in body.lower() or "Within the usual range" in body
    from kareshafaa.comms import event_mail

    _reset()
    p = db.get(Patient, pid)
    rep = db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == pid)
                     .order_by(DiagnosticReport.id.desc())).first()
    event_mail.send_for_result(db, report=rep, patient=p, lang="en")
    assert FakeSMTP.sent == []


def test_placeholder_and_consent_skip_the_send(db, new_case, mail_on, client, as_user):
    cid = new_case(value=250)["id"]
    db.expire_all()
    c = db.get(Case, cid)
    _give_email(db, c, "someone@example.com")
    res = send_with_ladder(db, c, "result_notification")
    assert res["email_sent"] is False and FakeSMTP.sent == []
    _give_email(db, c, REAL)
    db.add(Consent(patient_id=c.patient_id, scope="comm_email", decision="deny", recorded_at=utcnow()))
    db.flush()
    res = send_with_ladder(db, c, "result_notification")
    assert res["attempts"][0]["status"] == "not-done (no consent)"
    assert FakeSMTP.sent == []
    db.rollback()


def test_patient_request_confirm_sends_appointment_email(db, make_patient, mail_on, client, as_user):
    from kareshafaa.models import Organization, User

    pid = make_patient(language="ar")
    db.expire_all()
    p = db.get(Patient, pid)
    p.email = REAL
    u = db.scalars(select(User).where(User.patient_id == pid)).first()
    if u is None:
        u = User(username=f"evt.patient.{pid}", display_name="Event Patient", role="patient", patient_id=pid)
        db.add(u)
    db.commit()
    site = db.scalar(select(Organization.id).where(Organization.kind == "primary_care", Organization.active.is_(True)))
    h = as_user(u.username)
    r = client.post("/api/me/requests/screening", headers=h,
                    json={"programme": "cervical", "site_org_id": site, "days": ["mon"], "times": ["morning"]})
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    _reset()
    start = datetime(2026, 12, 1, 6, 0, 0)  # 10:00 UAE
    ok = client.post(f"/api/patient-requests/{rid}/confirm", headers=as_user("aisha.rahman"),
                     json={"start": start.isoformat() + "Z", "org_id": site})
    assert ok.status_code == 200, ok.text
    assert len(FakeSMTP.sent) == 1
    body, html = _plain(), _html()
    assert "10:00" in body and ("توقيت الإمارات" in body or "UAE time" in body)
    assert "KareShafaa" in FakeSMTP.sent[0]["Subject"]
    assert 'dir="rtl"' in html
    # the simulated in-app message is still recorded
    from kareshafaa.models import PatientRequest

    db.expire_all()
    req = db.get(PatientRequest, rid)
    sim = db.get(Communication, req.notified_comm_id)
    assert sim.simulated and sim.template_key == "appointment_request.confirmed"


def test_follow_up_request_confirm_emails_via_the_ladder(db, client, as_user, new_case, mail_on):
    from kareshafaa.models import User

    cid, co, *_ = _to_accepted(client, as_user, db, new_case)
    db.expire_all()
    c = db.get(Case, cid)
    if c.target_due_at is None or c.target_due_at < utcnow() + timedelta(days=3):
        c.target_due_at = utcnow() + timedelta(days=10)
        db.commit()
    u = db.scalars(select(User).where(User.patient_id == c.patient_id)).first()
    if u is None:
        u = User(username=f"evt.fu.{c.patient_id}", display_name="FU Patient", role="patient",
                 patient_id=c.patient_id)
        db.add(u)
        db.commit()
    h = as_user(u.username)
    prof = client.get("/api/me/profile", headers=h)
    assert prof.status_code == 200, prof.text
    slot = next(x for x in prof.json()["loop"] if x.get("case_id") == cid)["choose_time"]["slots"][0]
    r = client.post("/api/me/requests/follow-up", headers=h, json={"case_id": cid, "slot": slot})
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    _reset()
    ok = client.post(f"/api/patient-requests/{rid}/confirm", headers=co, json={})
    assert ok.status_code == 200, ok.text
    assert ok.json()["appointment_id"]
    assert len(FakeSMTP.sent) == 1
    body = _plain()
    assert f"Case #{cid}" in body
    assert "UAE time (Asia/Dubai)" in body or "توقيت الإمارات" in body


def test_graph_sends_html_when_an_alternative_is_present(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "smtp_password", None)
    monkeypatch.setattr(s, "mail_from", "kareshafaa_com@outlook.com")
    monkeypatch.setattr(s, "mail_oauth_client_id", "00000000-test-client")
    monkeypatch.setattr(s, "mail_oauth_refresh_token", "rt-not-real")
    monkeypatch.setattr(s, "mail_transport", "auto")
    monkeypatch.setattr(s, "mail_allow_synthetic", False)
    monkeypatch.setattr(mail, "_TOKEN", {})
    seen = []

    def fake_post(url, data, timeout):
        return {"access_token": "at-not-real", "expires_in": 3600}

    def fake_json(url, payload, token, timeout):
        seen.append(payload)
        return 202

    monkeypatch.setattr(mail, "_http_post_form", fake_post)
    monkeypatch.setattr(mail, "_http_post_json", fake_json)
    mail.send_email("priyam.check@realmail.ae", "KareShafaa: your appointment is booked",
                    "Hello Emma,\nDate and time: Sunday, 15 November 2026 at 13:00 UAE time (Asia/Dubai)\n",
                    html_body='<html dir="ltr"><body><p>Hello Emma</p><p>13:00 UAE time (Asia/Dubai)</p></body></html>')
    assert seen[0]["message"]["body"]["contentType"] == "HTML"
    assert "13:00 UAE time" in seen[0]["message"]["body"]["content"]
    # plain-only still goes as Text (existing staff / alert e-mails)
    seen.clear()
    mail.send_email("priyam.check@realmail.ae", "KareShafaa: test email", "hello")
    assert seen[0]["message"]["body"]["contentType"] == "Text"
    assert seen[0]["message"]["body"]["content"].strip() == "hello"


def test_safe_send_never_raises(db, new_case):
    cid = new_case(value=250)["id"]
    db.expire_all()
    c = db.get(Case, cid)
    # 'issued' + review_required would raise from send_with_ladder; safe_send swallows it.
    c.review_required = True
    c.state = "issued"
    db.flush()
    from kareshafaa.security import SYSTEM

    out = safe_send_with_ladder(db, c, "result_notification", SYSTEM)
    assert out["email_sent"] is False and out.get("failed") is True
