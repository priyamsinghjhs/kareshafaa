"""Iteration 7 failure-mode round: time zones, day boundaries and clock skew.

Stored datetimes are naive UTC; the UAE working calendar is local (UTC+4, no DST). Findings fixed this round:
the SLA calendar used the UTC date (Fri 20:00-24:00 UAE = Saturday was counted as Friday working time), an
appointment time with an offset had the offset dropped, the patient's confirmation showed UTC, and attendance or
no-show could be recorded before the appointment time."""
from datetime import datetime, timedelta, timezone

import pytest

from wasl.calendar_uae import (add_working_hours, add_working_hours_utc, as_utc_naive, local_display, to_local,
                               to_utc)
from wasl.sla import due_at


def test_local_round_trip_and_no_dst():
    for m in range(1, 13):  # Asia/Dubai: +4 all year, no DST discontinuity
        t = datetime(2026, m, 15, 12, 0)
        assert to_local(t) - t == timedelta(hours=4) and to_utc(to_local(t)) == t
    # around European / US DST switch dates nothing changes either
    for t in (datetime(2026, 3, 29, 1, 30), datetime(2026, 10, 25, 1, 30), datetime(2026, 3, 8, 7), datetime(2026, 11, 1, 6)):
        assert to_local(t) - t == timedelta(hours=4)


def test_day_boundary_uses_uae_local_date():
    # Friday 21:00 UTC is already Saturday 01:00 in the UAE: a 24 working-hour clock starts Monday 00:00 UAE
    fri_late_utc = datetime(2026, 10, 23, 21, 0)
    assert add_working_hours_utc(fri_late_utc, 24) == to_utc(datetime(2026, 10, 27, 0, 0))
    # the old (UTC-date) behaviour would have counted 3 h of "Friday" and ended Monday 21:00 UTC
    assert add_working_hours(fri_late_utc, 24) != add_working_hours_utc(fri_late_utc, 24)
    # due_at for working-day tiers applies the local calendar; 24/7 tiers are unaffected
    assert due_at(fri_late_utc, 24, "routine") == add_working_hours_utc(fri_late_utc, 24)
    assert due_at(fri_late_utc, 24, "critical") == fri_late_utc + timedelta(hours=24)


def test_holiday_boundary_local():
    # 1 Dec 2026 (Commemoration Day) starts at 30 Nov 20:00 UTC
    start = datetime(2026, 11, 30, 19, 0)  # Mon 23:00 UAE, 1 h of working time left
    end = add_working_hours_utc(start, 2)
    assert to_local(end).date() == datetime(2026, 12, 4).date()  # Dec 1-3 holidays, resumes Fri 4 Dec


def test_offset_converted_not_dropped():
    gst = timezone(timedelta(hours=4))
    assert as_utc_naive(datetime(2026, 10, 25, 9, 0, tzinfo=gst)) == datetime(2026, 10, 25, 5, 0)
    assert as_utc_naive(datetime(2026, 10, 25, 5, 0)) == datetime(2026, 10, 25, 5, 0)
    assert local_display(datetime(2026, 10, 25, 5, 0)) == "25/10/2026 09:00"
    assert local_display(datetime(2026, 10, 25, 21, 30)) == "26/10/2026 01:30"  # local date rolls over


def test_appointment_offset_and_patient_time(client, as_user, db, new_case):
    """End to end through the API: a +04:00 time is converted, the confirmation shows UAE local time, and
    attendance cannot be recorded before the appointment."""
    from sqlalchemy import select

    from tests.test_journey_api import SUMMARY, _post, notify_and_ack
    from wasl.models import Appointment, Communication

    cid = new_case("colorectal_fit", 250)["id"]
    co, gp, sp = as_user("aisha.rahman"), as_user("omar.haddad"), as_user("vikram.mehta")
    notify_and_ack(client, as_user, cid)
    assert _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed").status_code == 200
    assert _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, summary=SUMMARY).status_code == 200
    assert _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="accepted").status_code == 200
    gst = timezone(timedelta(hours=4))
    start_local = (datetime.now(timezone.utc) + timedelta(days=2)).astimezone(gst).replace(hour=9, minute=0, second=0,
                                                                                           microsecond=0)
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=start_local.isoformat())
    assert r.status_code == 200, r.text
    db.expire_all()
    appt = db.scalar(select(Appointment).where(Appointment.case_id == cid))
    assert appt.start == start_local.astimezone(timezone.utc).replace(tzinfo=None)  # 05:00 UTC, not 09:00 UTC
    conf = [m for m in db.scalars(select(Communication).where(Communication.case_id == cid))
            if m.category == "appointment_confirmation"]
    assert conf and all(start_local.strftime("%d/%m/%Y 09:00") in m.content for m in conf if m.channel != "phone")
    r = _post(client, co, f"/api/cases/{cid}/attendance", attended=False, barrier_code="transport")
    assert r.status_code == 422 and "before the appointment" in r.json()["detail"]
    assert _post(client, sp, f"/api/cases/{cid}/attendance", attended=True).status_code == 422


def test_attendance_timing_rules(db, new_case):
    from wasl.models import Appointment, Case
    from wasl.security import Actor
    from wasl.services import workflow as wf
    from wasl.services.errors import Invalid

    c = db.get(Case, new_case(value=250)["id"])
    from wasl.models import ServiceRequest

    c.state = "appointment_booked"
    sr = ServiceRequest(case_id=c.id, kind="referral", code="colonoscopy", requester_id=c.owner_id, status="accepted",
                        performer_org_id=9)
    db.add(sr)
    db.flush()
    start = datetime(2026, 10, 25, 5, 0)
    db.add(Appointment(case_id=c.id, service_request_id=sr.id, org_id=9, start=start, status="booked",
                       created_at=datetime(2026, 10, 20)))
    db.flush()
    sp = Actor(user_id=1, role="specialist", display="Spec")
    with pytest.raises(Invalid):
        wf.record_attendance(db, c, sp, False, barrier_code="transport", now=start - timedelta(minutes=1))
    with pytest.raises(Invalid):
        wf.record_attendance(db, c, sp, True, now=start - timedelta(hours=wf.ATTENDANCE_EARLY_HOURS, minutes=1))
    a = wf.record_attendance(db, c, sp, True, now=start - timedelta(hours=1))  # early arrival is fine
    assert a.status == "fulfilled"
    db.rollback()


def test_hl7_ack_timestamp_carries_offset(client, as_user, db, make_patient):
    from wasl.models import Patient

    mrn = db.get(Patient, make_patient()).mrn
    m = "\r".join(["MSH|^~\\&|LIS|LAB|WASL|PROG|20261020093000||ORU^R01^ORU_R01|TZ-1|P|2.5.1",
                   f"PID|1||{mrn}^^^WASL^MR||SYN^PAT||19700101|F", "OBX|1|NM|29771-3^FIT^LN||5|ug/g|||||F"])
    ack = client.post("/api/ingest/hl7v2", headers={**as_user("aisha.rahman"), "Content-Type": "text/plain"},
                      content=m).json()["ack"]
    assert ack.split("|")[6].endswith("+0000")
