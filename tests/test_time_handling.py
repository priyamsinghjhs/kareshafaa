"""Iteration 7 failure-mode round: time zones, day boundaries and clock skew.

Stored datetimes are naive UTC; the UAE working calendar is local (UTC+4, no DST). Findings fixed this round:
the SLA calendar used the UTC date (Fri 20:00-24:00 UAE = Saturday was counted as Friday working time), an
appointment time with an offset had the offset dropped, the patient's confirmation showed UTC, and attendance or
no-show could be recorded before the appointment time."""
from datetime import datetime, timedelta, timezone

import pytest

from kareshafaa.calendar_uae import (add_working_hours, add_working_hours_utc, as_utc_naive, local_display, to_local,
                               to_utc)
from kareshafaa.sla import due_at


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
    from kareshafaa.models import Appointment, Communication

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
    from kareshafaa.models import Appointment, Case
    from kareshafaa.security import Actor
    from kareshafaa.services import workflow as wf
    from kareshafaa.services.errors import Invalid

    c = db.get(Case, new_case(value=250)["id"])
    from kareshafaa.models import ServiceRequest

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
    from kareshafaa.models import Patient

    mrn = db.get(Patient, make_patient()).mrn
    m = "\r".join(["MSH|^~\\&|LIS|LAB|KARESHAFAA|PROG|20261020093000||ORU^R01^ORU_R01|TZ-1|P|2.5.1",
                   f"PID|1||{mrn}^^^KARESHAFAA^MR||SYN^PAT||19700101|F", "OBX|1|NM|29771-3^FIT^LN||5|ug/g|||||F"])
    ack = client.post("/api/ingest/hl7v2", headers={**as_user("aisha.rahman"), "Content-Type": "text/plain"},
                      content=m).json()["ack"]
    assert ack.split("|")[6].endswith("+0000")


# ---- one clinic clock everywhere (staff UI, patient UI, messages, errors, database). Found: staff pages formatted and
# parsed times in the BROWSER's zone (a coordinator in India typing 21:00 on the case page booked 21:00 IST = 19:30
# UAE while the queue page booked 21:00 UAE and the patient was told UAE time), and the booking form labelled the
# pathway target with the UTC date (2026-10-05) while the server refused with the UAE date (05/10/2026).
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

COMMON_JS = Path(__file__).resolve().parents[1] / "kareshafaa/static/js/common.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
_STUBS = ("const src = require('fs').readFileSync(process.argv[1], 'utf8');"
          "const doc = { getElementById() { return null; }, addEventListener() {}, querySelector() { return null; },"
          " querySelectorAll() { return []; }, documentElement: { dataset: {}, classList: { add() {}, remove() {} } } };"
          "const store = { getItem() { return null; }, setItem() {} };"
          "const W = new Function('window', 'document', 'location', 'navigator', 'localStorage', 'sessionStorage',"
          " src + '; return W;')({ addEventListener() {}, matchMedia() { return { matches: false, addEventListener() {} }; } },"
          " doc, { pathname: '/', search: '' }, {}, store, store);")


def run_w(expr: str, tz: str = "Asia/Calcutta"):
    """Evaluate an expression against W from common.js in node, with the browser in time zone `tz`."""
    import os

    out = subprocess.run([NODE, "-e", _STUBS + f"console.log(JSON.stringify(({expr})));", str(COMMON_JS)],
                         capture_output=True, text=True, timeout=30, env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
@pytest.mark.parametrize("tz", ["Asia/Calcutta", "UTC", "America/New_York", "Asia/Dubai"])
def test_staff_clock_is_uae_whatever_the_browser_zone(tz):
    got = run_w("[W.utcIsoFromLocalInput('2027-01-17T21:00'), W.utcIsoFromLocalInput('2027-01-18T02:30'),"
                " W.localInputFromUtc('2027-01-17T17:00:00'), W.localInputFromUtc('2027-01-17T22:30:00.123456'),"
                " W.dmy('2026-10-05T19:59:00'), W.dmy('2026-10-05T20:00:00'), W.dmy('2026-10-05'),"
                " W.utcIsoFromLocalDate('2026-11-10'), W.localInputFromUtc(W.utcIsoFromLocalInput('2026-03-29T01:30')),"
                " W.dt('2027-01-17T17:00:00'), W.dt('2027-01-17T17:00:00Z') === W.dt('2027-01-17T21:00:00+04:00')]", tz)
    assert got[0] == "2027-01-17T17:00:00.000Z"      # 21:00 UAE = 17:00 UTC (not 15:30 UTC = 21:00 IST)
    assert got[1] == "2027-01-17T22:30:00.000Z"      # early-morning UAE is the previous UTC day
    assert got[2] == "2027-01-17T21:00" and got[3] == "2027-01-18T02:30"
    assert got[4] == "05/10/2026" and got[5] == "06/10/2026" and got[6] == "05/10/2026"  # same as local_display[:10]
    assert got[7] == "2026-11-10T08:00:00.000Z"      # calendar date -> noon UAE, the same day in UTC and UAE
    assert got[8] == "2026-03-29T01:30"              # no DST anywhere (European switch night)
    assert "17" in got[9] and "2027" in got[9] and ("21:00" in got[9] or "09:00" in got[9])
    assert got[10] is True


@needs_node
def test_staff_input_helpers_refuse_bad_values():
    got = run_w("['', '2027-02-30T10:00', '2027-01-17', '17/01/2027 21:00', '2027-01-17T24:00'].map(s => {"
                " try { return W.utcIsoFromLocalInput(s); } catch (e) { return 'ERR:' + e.message; } })")
    assert all(g.startswith("ERR:Enter a valid date and time") for g in got)
    # relative helpers are absolute instants: naive (UTC) and offset forms of the same instant agree
    assert run_w("[W.ago('2020-01-01T00:00:00') === W.ago('2020-01-01T04:00:00+04:00'),"
                 " W.left('2020-01-01T00:00:00').text === W.left('2020-01-01T00:00:00Z').text]") == [True, True]


@needs_node
def test_appointment_round_trip_staff_form_db_patient_and_staff_display(client, as_user, db, new_case):
    """A coordinator in India types 21:00 in the (UAE) datetime-local field: the DB holds 17:00 naive UTC, the patient
    is told 21:00 UAE, and the staff page shows and pre-fills 21:00 again."""
    from sqlalchemy import select

    from kareshafaa.models import Appointment, Communication
    from tests.test_journey_api import SUMMARY, _post, notify_and_ack

    cid = new_case("colorectal_fit", 250)["id"]
    co, gp, sp = as_user("aisha.rahman"), as_user("omar.haddad"), as_user("vikram.mehta")
    notify_and_ack(client, as_user, cid)
    assert _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed").status_code == 200
    assert _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, summary=SUMMARY).status_code == 200
    assert _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="accepted").status_code == 200
    day = (datetime.now(timezone.utc) + timedelta(days=3)).astimezone(timezone(timedelta(hours=4))).date()
    typed = f"{day.isoformat()}T21:00"
    start = run_w(f"W.utcIsoFromLocalInput('{typed}')")  # exactly what case.js / queue.js send
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=start, beyond_target_reason="earliest available slot")
    assert r.status_code == 200, r.text
    db.expire_all()
    appt = db.scalar(select(Appointment).where(Appointment.case_id == cid))
    assert appt.start == datetime.combine(day, datetime.min.time()).replace(hour=17)  # naive UTC = 21:00 - 4 h
    conf = [m for m in db.scalars(select(Communication).where(Communication.case_id == cid))
            if m.category == "appointment_confirmation" and m.channel != "phone"]
    assert conf and all(day.strftime("%d/%m/%Y") + " 21:00" in m.content for m in conf)
    staff = client.get(f"/api/cases/{cid}", headers=co).json()
    api_start = next(a["start"] for a in staff["appointments"] if a["id"] == appt.id)
    assert api_start == appt.start.isoformat()  # naive UTC on the wire, never a naive local time
    assert run_w(f"W.localInputFromUtc('{api_start}')") == typed
    shown = run_w(f"W.dt('{api_start}')")
    assert ("21:00" in shown or "09:00" in shown) and str(day.day) in shown
    assert run_w(f"W.dmy('{staff['target_due_at']}')") == local_display(datetime.fromisoformat(staff["target_due_at"]))[:10]


def test_pathway_target_error_when_target_already_passed():
    """Booking after a missed target still needs the reason; the message no longer says 'book earlier'."""
    from types import SimpleNamespace

    from kareshafaa.services.errors import Invalid
    from kareshafaa.services.workflow import _check_start

    now = datetime(2026, 11, 10, 16, 0)
    missed = SimpleNamespace(target_due_at=datetime(2026, 10, 5, 8, 0))
    start = datetime(2027, 1, 17, 17, 0)
    with pytest.raises(Invalid) as e:
        _check_start(missed, start, now, None)
    assert "already passed" in str(e.value) and "05/10/2026" in str(e.value) and "book earlier" not in str(e.value)
    with pytest.raises(Invalid):
        _check_start(missed, start, now, "too short")
    assert _check_start(missed, start, now, "earliest available slot") is True
    future = SimpleNamespace(target_due_at=datetime(2026, 11, 20, 20, 30))  # 21/11 in the UAE
    with pytest.raises(Invalid) as e:
        _check_start(future, datetime(2026, 11, 25, 5, 0), now, "")
    assert "book earlier" in str(e.value) and "21/11/2026" in str(e.value)
    assert _check_start(future, datetime(2026, 11, 18, 5, 0), now, None) is False


def test_gp_summary_dates_use_uae_calendar():
    from datetime import date

    from kareshafaa.services.gp_summary import _d

    assert _d(datetime(2027, 1, 16, 21, 0)) == "17 Jan 2027"  # 01:00 UAE on the 17th
    assert _d(date(2027, 1, 16)) == "16 Jan 2027" and _d(None) is None
