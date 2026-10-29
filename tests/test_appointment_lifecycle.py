"""Appointment lifecycle: reschedule and cancel with reason/barrier, reminder (manual + sweep), message templates."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.test_journey_api import SUMMARY, notify_and_ack


def _p(client, h, url, **body):
    return client.post(url, headers=h, json=body)


def _booked(client, as_user, new_case):
    from kareshafaa.models import utcnow
    cid = new_case(value=250)["id"]
    co, gp, sp = (as_user(u) for u in ("aisha.rahman", "omar.haddad", "vikram.mehta"))
    notify_and_ack(client, as_user, cid)
    assert _p(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed").status_code == 200
    assert _p(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, performer_id=31, summary=SUMMARY).status_code == 200
    assert _p(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="ok").status_code == 200
    start = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    r = _p(client, co, f"/api/cases/{cid}/appointment", start=start.isoformat())
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "appointment_booked"
    return cid, co, gp, sp, start


def test_reschedule_keeps_case_booked_and_records_barrier(client, db, as_user, new_case):
    from kareshafaa.models import Appointment, AuditEvent, Communication, Lesson
    cid, co, *_rest, start = _booked(client, as_user, new_case)
    new_start = (start + timedelta(days=2)).isoformat()
    r = _p(client, co, f"/api/cases/{cid}/appointment/reschedule", start=new_start,
           reason_code="patient_request", barrier_code="transport", note="taxi not available that morning")
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "appointment_booked"
    db.expire_all()
    rows = db.scalars(select(Appointment).where(Appointment.case_id == cid).order_by(Appointment.id)).all()
    assert len(rows) == 2 and rows[0].status == "cancelled" and rows[1].status == "booked"
    assert rows[0].cancel_reason == "patient_request" and rows[0].barrier_code == "transport"
    assert rows[1].rescheduled_from_id == rows[0].id
    assert db.scalar(select(Lesson).where(Lesson.case_id == cid, Lesson.kind == "appointment_change"))
    assert db.scalar(select(Communication).where(Communication.case_id == cid, Communication.category == "appointment_changed"))
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "appointment.rescheduled", AuditEvent.case_id == cid))


def test_cancel_returns_to_clinic_accepted_for_rebook(client, db, as_user, new_case):
    from kareshafaa.models import Appointment, Communication
    cid, co, *_ = _booked(client, as_user, new_case)
    r = _p(client, co, f"/api/cases/{cid}/appointment/cancel", reason_code="clinic_cancelled",
           note="Endoscopist on leave")
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "referral_accepted"
    db.expire_all()
    appt = db.scalars(select(Appointment).where(Appointment.case_id == cid).order_by(Appointment.id.desc())).first()
    assert appt.status == "cancelled" and appt.cancel_reason == "clinic_cancelled"
    assert db.scalar(select(Communication).where(Communication.case_id == cid, Communication.category == "appointment_cancelled"))
    # rebook works
    from kareshafaa.models import utcnow
    start = (utcnow() + timedelta(days=5)).isoformat()
    assert _p(client, co, f"/api/cases/{cid}/appointment", start=start).status_code == 200


def test_patient_request_requires_barrier_and_clinical_needs_note(client, as_user, new_case):
    cid, co, *_rest, start = _booked(client, as_user, new_case)
    assert _p(client, co, f"/api/cases/{cid}/appointment/cancel", reason_code="patient_request").status_code == 422
    assert _p(client, co, f"/api/cases/{cid}/appointment/reschedule", start=(start + timedelta(days=1)).isoformat(),
              reason_code="clinical_reason").status_code == 422
    assert _p(client, co, f"/api/cases/{cid}/appointment/cancel", reason_code="clinical_reason",
              note="Wrong site booked").status_code == 200


def test_manual_and_sweep_reminder(client, db, as_user, new_case):
    from kareshafaa.models import Appointment, Communication, utcnow
    from kareshafaa.services.escalation import run_sweep
    cid, co, *_rest, start = _booked(client, as_user, new_case)
    # move appointment into the 48h reminder window
    appt = db.scalars(select(Appointment).where(Appointment.case_id == cid, Appointment.status == "booked")).one()
    appt.start = utcnow() + timedelta(hours=24)
    db.commit()
    r = _p(client, co, f"/api/cases/{cid}/appointment/remind")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalar(select(Communication).where(Communication.case_id == cid, Communication.category == "appointment_reminder"))
    assert db.get(Appointment, appt.id).reminder_sent_at is not None
    # a second case for the sweep path
    cid2, co2, *_r, _s = _booked(client, as_user, new_case)
    a2 = db.scalars(select(Appointment).where(Appointment.case_id == cid2, Appointment.status == "booked")).one()
    a2.start = utcnow() + timedelta(hours=12)
    a2.reminder_sent_at = None
    db.commit()
    # force inside patient message window by using a UAE daytime now if needed
    from kareshafaa.services import escalation as esc
    from kareshafaa.calendar_uae import to_local
    now = utcnow()
    local = to_local(now)
    if not (8 <= local.hour < 21):
        # invent a UTC moment that is 10:00 UAE
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        now = datetime(2026, 6, 15, 10, 0, tzinfo=ZoneInfo("Asia/Dubai")).astimezone(timezone.utc).replace(tzinfo=None)
        a2.start = now + timedelta(hours=12)
        db.commit()
    out = run_sweep(db, now=now, case_ids=[cid2])
    db.commit()
    db.expire_all()
    assert db.get(Appointment, a2.id).reminder_sent_at is not None
    assert out.get("appointment_reminders", 0) >= 1 or db.scalar(
        select(Communication).where(Communication.case_id == cid2, Communication.category == "appointment_reminder"))


def test_templates_all_languages():
    from kareshafaa.comms.content import TEMPLATES
    for lang in ("en", "ar", "hi", "ur"):
        for cat in ("appointment_reminder", "appointment_changed", "appointment_cancelled"):
            assert "standard" in TEMPLATES[lang][cat] and "simplified" in TEMPLATES[lang][cat]
            assert "{date}" in TEMPLATES[lang][cat]["standard"]
