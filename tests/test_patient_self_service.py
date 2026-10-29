"""Patient app self-service: own contact details (audited, same rules as staff) and appointment / result history."""
from __future__ import annotations

from sqlalchemy import select

from kareshafaa.comms.self_service_text import SELF_TEXT
from kareshafaa.models import AuditEvent, Patient, User

from tests.test_patient_registration import person

CO = "aisha.rahman"


def _patient_login(client, as_user, db, **kw):
    h = as_user(CO)
    pid = client.post("/api/patients", headers=h, json=person(**kw)).json()["patient_id"]
    assert client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "in_person"}).status_code == 200
    db.expire_all()
    u = db.scalar(select(User).where(User.patient_id == pid))
    u.active = True
    db.commit()
    return pid, as_user(u.username)


def test_text_has_every_key_in_four_languages():
    keys = set(SELF_TEXT["en"])
    for lang in ("ar", "hi", "ur"):
        assert set(SELF_TEXT[lang]) == keys, lang
        for k, v in SELF_TEXT["en"].items():
            if isinstance(v, dict):
                assert set(SELF_TEXT[lang][k]) == set(v), (lang, k)
    assert any("\u0600" <= ch <= "\u06ff" for ch in SELF_TEXT["ur"]["save"])
    assert any("\u0900" <= ch <= "\u097f" for ch in SELF_TEXT["hi"]["save"])


def test_patient_updates_own_contact_with_staff_rules_and_audit(client, as_user, db):
    pid, ph = _patient_login(client, as_user, db, language="ur")
    prof = client.get("/api/me/profile", headers=ph).json()
    assert prof["language"] == "ur" and prof["rtl"] and prof["text"]["save"] == SELF_TEXT["ur"]["save"]
    assert prof["contact"]["phone"] == "+971501234567"
    assert client.post("/api/me/contact", headers=ph, json={"phone": "12345"}).status_code == 422
    assert client.post("/api/me/contact", headers=ph, json={"email": "not-an-email"}).status_code == 422
    assert client.post("/api/me/contact", headers=ph, json={"phone": None, "preferred_channel": "sms"}).status_code == 422
    assert client.post("/api/me/contact", headers=ph, json={"emirate": "Abu Dhabi", "region": None}).status_code == 422
    assert client.post("/api/me/contact", headers=ph, json={"given_name": "Other"}).status_code == 422  # not editable
    r = client.post("/api/me/contact", headers=ph, json={"phone": "055 222 3344", "email": "me@example.ae",
                                                         "address_line": "Flat 9, Building 2", "city": "Dubai Marina",
                                                         "emirate": "Dubai", "region": "Al Ain",
                                                         "preferred_channel": "whatsapp", "whatsapp": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    p = db.get(Patient, pid)
    assert (p.phone, p.email, p.emirate, p.region, p.preferred_channel, p.whatsapp) == \
        ("+971552223344", "me@example.ae", "Dubai", "Dubai", "whatsapp", True)  # region = emirate outside Abu Dhabi (staff rule)
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "patient.self_contact_updated",
                                             AuditEvent.entity_id == str(pid))).all()
    assert len(ev) == 1 and ev[0].actor_role == "patient" and "phone" in ev[0].detail["fields"]
    rec = client.get(f"/api/patients/{pid}", headers=as_user(CO)).json()
    assert rec["contact"]["phone"] == "+971552223344"
    assert any(c["event"] == "patient.self_contact_updated" and c["role"] == "patient" for c in rec["changes"])
    assert client.post("/api/me/contact", headers=ph, json={"phone": "055 222 3344"}).json()["changed"] == []


def test_only_patients_and_only_their_own_record(client, as_user):
    for u in (CO, "omar.haddad", "admin", "analyst"):
        assert client.post("/api/me/contact", headers=as_user(u), json={"email": "x@example.ae"}).status_code == 403, u
        assert client.get("/api/me/profile", headers=as_user(u)).status_code == 403, u
    # the body cannot point at another record: there is no patient id field at all
    assert client.post("/api/me/contact", headers=as_user("patient.en"), json={"patient_id": 1}).status_code == 422


def test_history_shows_appointments_and_results_without_premature_news(client, as_user, db, new_case):
    from tests.test_appointment_lifecycle import _booked
    from kareshafaa.models import Case

    cid, co, *_rest, start = _booked(client, as_user, new_case)
    c = db.get(Case, cid)
    u = db.scalar(select(User).where(User.patient_id == c.patient_id))
    if u is None:
        u = User(username=f"self.{c.patient_id}", display_name="Self Test", role="patient", patient_id=c.patient_id,
                 active=True)
        db.add(u)
        db.commit()
    ph = as_user(u.username)
    for lang in ("en", "ar", "hi", "ur"):
        h = client.get(f"/api/me/profile?lang={lang}", headers=ph).json()["history"]
        assert h["appointments"] and h["appointments"][0]["status"] == "upcoming"
        assert h["appointments"][0]["status_text"] == SELF_TEXT[lang]["appt_status"]["upcoming"]
        assert h["results"][0]["outcome"] == "follow_up"
        assert h["results"][0]["outcome_text"] == SELF_TEXT[lang]["outcome"]["follow_up"]
    # a result the team has not yet told the patient about is shown as "being looked at", never as the outcome
    from kareshafaa.models import DiagnosticReport
    nc = new_case(value=300)
    c2 = db.get(Case, nc["id"])
    assert c2.state in ("issued", "triaged")
    u2 = User(username=f"self.{c2.patient_id}", display_name="Self Test 2", role="patient", patient_id=c2.patient_id, active=True)
    db.add(u2)
    db.commit()
    h2 = client.get("/api/me/profile?lang=en", headers=as_user(u2.username)).json()["history"]
    assert [r["outcome"] for r in h2["results"]] == ["reviewing"]
    assert "value" not in h2["results"][0] and "note" not in str(h2).lower()
    assert db.scalar(select(DiagnosticReport.id).where(DiagnosticReport.case_id == c2.id))


def test_patient_page_loads_profile_script(client, as_user):
    r = client.get("/patient", headers=as_user("patient.en"))
    assert r.status_code == 200 and "/static/js/patient_profile.js" in r.text
    js = client.get("/static/js/patient_profile.js").text
    assert "/api/me/contact" in js and "/api/me/profile" in js
