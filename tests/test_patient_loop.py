"""Patient app: place in the screening-to-care loop, appointment answers (confirm / ask to change - never moves it),
messages (simulated, no staff call notes) and own contact consent. Server-side: a patient only ever acts on their
own appointments; staff RBAC unchanged."""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from sqlalchemy import select

from kareshafaa.models import (Appointment, AppointmentRequest, AuditEvent, Case, Communication, Lesson, User,
                               utcnow)
from kareshafaa.statemachine import S

_n = itertools.count()


@pytest.fixture()
def booked(db, as_user):
    """A patient (own app login) whose case has a booked appointment a few days ahead."""
    mine = {u.patient_id: u for u in db.scalars(select(User).where(User.username.like("loop.patient%")))}
    used = set(db.scalars(select(User.patient_id).where(User.patient_id.is_not(None)))) - set(mine)
    for c in db.scalars(select(Case).where(Case.state == S.APPOINTMENT_BOOKED).order_by(Case.id)):
        if c.patient_id in used:
            continue
        a = db.scalars(select(Appointment).where(Appointment.case_id == c.id, Appointment.status == "booked")
                       .order_by(Appointment.id.desc())).first()
        if a is None:
            continue
        a.start = utcnow() + timedelta(days=5)
        for r in db.scalars(select(AppointmentRequest).where(AppointmentRequest.case_id == c.id, AppointmentRequest.status == "open")):
            r.status = "handled"  # left over from an earlier test on the same case
        u = mine.get(c.patient_id)
        if u is None:
            u = User(username=f"loop.patient{next(_n)}.{c.id}", display_name="Loop Patient", role="patient", patient_id=c.patient_id)
            db.add(u)
        db.commit()
        return {"case": c.id, "appt": a.id, "start": a.start, "h": as_user(u.username), "patient_id": c.patient_id}
    pytest.skip("no booked appointment in the test data")


def test_profile_has_loop_messages_consent_and_options(client, booked):
    d = client.get("/api/me/profile", headers=booked["h"]).json()
    assert d["loop"] and d["simulated_note"] is True
    lp = next(x for x in d["loop"] if x.get("case_id") == booked["case"]) if any("case_id" in x for x in d["loop"]) else d["loop"][0]
    assert lp["programme_name"]
    assert set(d["options"]["barriers"]) >= {"work", "transport", "childcare"}
    assert set(d["consent"]) == {"comm_app", "comm_sms", "comm_whatsapp", "comm_phone", "comm_email", "care_coordination"}
    for m in d["messages"]:
        assert m["simulated"] is not None
        if m["channel"] == "phone":
            assert m["text"] is None  # staff call notes never shown
        if m["category"] == "account_invite":
            assert m["text"] is None  # activation code not repeated


def test_confirm_is_stored_audited_and_idempotent(client, db, booked):
    u = f"/api/me/appointments/{booked['appt']}/confirm"
    r1 = client.post(u, headers=booked["h"], json={})
    r2 = client.post(u, headers=booked["h"], json={})
    assert r1.status_code == 200 and r2.json()["request_id"] == r1.json()["request_id"]
    db.expire_all()
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "appointment.patient_confirmed",
                                                 AuditEvent.entity_id == str(booked["appt"])))


def test_change_request_never_moves_the_appointment_and_reaches_staff(client, db, booked, as_user):
    u = f"/api/me/appointments/{booked['appt']}/change-request"
    assert client.post(u, headers=booked["h"], json={"barrier_code": "not-a-reason"}).status_code == 422
    r = client.post(u, headers=booked["h"], json={"barrier_code": "work", "note": "Shift changed",
                                                   "preferred_times": "Mornings"})
    assert r.status_code == 200 and r.json()["appointment_moved"] is False
    db.expire_all()
    a = db.get(Appointment, booked["appt"])
    assert a.status == "booked" and abs((a.start - booked["start"]).total_seconds()) < 1
    req = db.get(AppointmentRequest, r.json()["request_id"])
    assert (req.kind, req.barrier_code, req.note, req.preferred_times, req.status) == ("change", "work", "Shift changed", "Mornings", "open")
    assert db.scalar(select(Lesson.id).where(Lesson.case_id == booked["case"], Lesson.kind == "appointment_change_request"))
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "appointment.change_requested",
                                                 AuditEvent.entity_id == str(booked["appt"])))
    # a second request (or a confirm) while one is open is refused
    assert client.post(u, headers=booked["h"], json={"barrier_code": "transport"}).status_code == 409
    assert client.post(f"/api/me/appointments/{booked['appt']}/confirm", headers=booked["h"], json={}).status_code == 409
    # staff: case detail shows it; resolving needs a note; then it is handled
    lead = as_user("fatima.al.mansoori")
    det = client.get(f"/api/cases/{booked['case']}", headers=lead).json()
    assert any(x["id"] == req.id and x["status"] == "open" for x in det["patient_requests"])
    ru = f"/api/cases/{booked['case']}/appointment/requests/{req.id}/resolve"
    assert client.post(ru, headers=lead, json={"reason": "x"}).status_code == 422
    assert client.post(ru, headers=lead, json={"reason": "Phoned: keeps the time"}).status_code == 200
    db.expire_all()
    assert db.get(AppointmentRequest, req.id).status == "handled"


def test_staff_cancel_closes_the_open_request(client, db, booked, as_user):
    r = client.post(f"/api/me/appointments/{booked['appt']}/change-request", headers=booked["h"], json={"barrier_code": "childcare"})
    rid = r.json()["request_id"]
    lead = as_user("fatima.al.mansoori")
    assert client.post(f"/api/cases/{booked['case']}/appointment/cancel", headers=lead,
                       json={"reason_code": "patient_request", "barrier_code": "childcare"}).status_code == 200
    db.expire_all()
    req = db.get(AppointmentRequest, rid)
    assert req.status == "handled" and req.handled_note == "appointment cancelled"


def test_patient_cannot_answer_someone_elses_appointment(client, db, booked):
    other = db.scalar(select(Appointment.id).join(Case, Case.id == Appointment.case_id)
                      .where(Case.patient_id != booked["patient_id"], Appointment.status == "booked").limit(1))
    for path, body in ((f"/api/me/appointments/{other}/confirm", {}),
                       (f"/api/me/appointments/{other}/change-request", {"barrier_code": "work"})):
        assert client.post(path, headers=booked["h"], json=body).status_code == 404  # never reveals it exists


@pytest.mark.parametrize("user", ["aisha.rahman", "omar.haddad", "fatima.al.mansoori", "admin"])
def test_staff_cannot_use_patient_endpoints(client, as_user, booked, user):
    h = as_user(user)
    assert client.post(f"/api/me/appointments/{booked['appt']}/confirm", headers=h, json={}).status_code == 403
    assert client.post("/api/me/contact-consent", headers=h, json={"scope": "comm_sms", "decision": "deny"}).status_code == 403


def test_patient_own_contact_consent_is_stored_and_audited(client, db, booked):
    h = booked["h"]
    assert client.post("/api/me/contact-consent", headers=h, json={"scope": "bogus", "decision": "deny"}).status_code == 422
    r = client.post("/api/me/contact-consent", headers=h, json={"scope": "comm_sms", "decision": "deny"})
    assert r.status_code == 200 and r.json()["consent"]["comm_sms"]["decision"] == "deny"
    assert r.json()["consent"]["comm_sms"]["by_patient"] is True
    r = client.post("/api/me/contact-consent", headers=h, json={"scope": "comm_sms", "decision": "permit"})
    assert r.json()["consent"]["comm_sms"]["decision"] == "permit"
    db.expire_all()
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "consent.recorded").order_by(AuditEvent.id.desc()))


def test_messages_never_show_staff_call_notes(client, db, booked):
    db.add(Communication(patient_id=booked["patient_id"], case_id=booked["case"], channel="phone", direction="outbound",
                         status="completed", content="STAFF NOTE: patient sounded worried", category="call",
                         sent_at=utcnow(), simulated=True))
    db.commit()
    txt = client.get("/api/me/profile", headers=booked["h"]).text
    assert "STAFF NOTE" not in txt
    assert "STAFF NOTE" not in client.get("/api/me/case", headers=booked["h"]).text


def test_patient_stop_contact_never_closes_and_keeps_the_case_owned(client, db, booked, as_user):
    """Item: a patient who stops contact from their own app. The case stays open and owned, a clinician is asked
    to review, automated outreach stops, and staff can still see and work the case."""
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.security import SYSTEM
    from kareshafaa.services.errors import DomainError
    h = booked["h"]
    try:
        r = client.post("/api/me/contact-consent", headers=h, json={"scope": "care_coordination", "decision": "deny"})
        assert r.status_code == 200, r.text
        db.expire_all()
        c = db.get(Case, booked["case"])
        assert c.state != S.CLOSED and c.owner_id is not None
        assert c.review_required and "consent_withdrawn" in (c.review_reasons or [])
        with pytest.raises(DomainError):
            send_with_ladder(db, c, "reminder", SYSTEM)
        db.rollback()
        assert client.get(f"/api/cases/{c.id}", headers=as_user("aisha.rahman")).status_code == 200
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "consent.recorded",
                                                     AuditEvent.case_id.is_(None) | (AuditEvent.case_id == c.id)).limit(1)) is not None
    finally:
        client.post("/api/me/contact-consent", headers=h, json={"scope": "care_coordination", "decision": "permit"})
