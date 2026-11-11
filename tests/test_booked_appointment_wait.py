"""A booked appointment waits for the visit (Priyam, case 1582): an appointment months ahead, booked after the
pathway target had passed (with a reason), is NOT overdue and does not escalate until the end of the appointment's
UAE day. If attendance is not recorded by then, the next day it is overdue and escalates (step clock ladder)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from kareshafaa.alert_policy import AlertDecision, decide
from kareshafaa.calendar_uae import to_local, to_utc
from kareshafaa.models import Case, Escalation, Lesson, StepTask, utcnow
from kareshafaa.services import workflow as wf
from kareshafaa.services.escalation import run_sweep
from tests.test_journey_api import SUMMARY, notify_and_ack


def _p(client, h, url, **body):
    return client.post(url, headers=h, json=body)


def _actor(db, username):
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.models import User

    return actor_from_user(db.scalar(select(User).where(User.username == username)))


def _accepted_past_target(client, db, as_user, new_case) -> Case:
    cid = new_case(value=250)["id"]
    gp, sp = as_user("omar.haddad"), as_user("vikram.mehta")
    notify_and_ack(client, as_user, cid)
    assert _p(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed").status_code == 200
    assert _p(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, performer_id=31, summary=SUMMARY).status_code == 200
    assert _p(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="ok").status_code == 200
    db.expire_all()
    case = db.get(Case, cid)
    case.tier = "critical"  # case 1582: critical result, pathway target already gone when the visit was booked
    case.target_due_at = utcnow() - timedelta(days=2)
    db.flush()
    return case


def _visit(days_ahead: int = 67) -> tuple[datetime, datetime]:
    """(start 19:30 UAE `days_ahead` from today, end of that UAE day), both naive UTC."""
    d = to_local(utcnow()).date() + timedelta(days=days_ahead)
    return to_utc(datetime.combine(d, datetime.min.time()).replace(hour=19, minute=30)), \
        to_utc(datetime.combine(d + timedelta(days=1), datetime.min.time()))


def _step(db, cid) -> StepTask:
    return db.scalar(select(StepTask).where(StepTask.case_id == cid, StepTask.status == "in-progress"))


def _n_esc(db, cid) -> int:
    return len(db.scalars(select(Escalation).where(Escalation.case_id == cid)).all())


def _queue_rag(db, actor, cid, now) -> tuple[str, bool]:
    from kareshafaa.services.queue import work_queue

    all_ = work_queue(db, actor, "all", now=now, search=f"#{cid}")
    over = work_queue(db, actor, "overdue", now=now, search=f"#{cid}")
    row = next(r for r in all_["items"] if r["id"] == cid)
    return row["sla_status"], any(r["id"] == cid for r in over["items"])


def _page_rag(db, case, actor, now) -> str:
    from kareshafaa.api.serializers import case_detail

    return case_detail(db, case, actor, now)["sla"]["status"]


def test_far_future_booking_after_missed_target_waits_for_the_visit_day(client, db, as_user, new_case):
    case = _accepted_past_target(client, db, as_user, new_case)
    co = _actor(db, "aisha.rahman")
    now = utcnow()
    start, day_end = _visit()
    wf.book_appointment(db, case, co, start, beyond_target_reason="As suggested by doctor", now=now)
    step = _step(db, case.id)
    assert case.state == "appointment_booked" and case.tier == "critical"  # tier (clinical urgency) unchanged
    assert step.due_at == case.due_at == day_end  # end of the appointment's UAE day, not the passed target
    assert step.inherited_breach and not step.breached  # the late booking stays recorded as a missed target
    n0 = _n_esc(db, case.id)
    for t in (now + timedelta(minutes=5), now + timedelta(days=30), day_end - timedelta(hours=1)):  # 23:00 UAE
        out = run_sweep(db, now=t, case_ids=[case.id])
        assert _n_esc(db, case.id) == n0 and not step.breached, (t, out)
        assert _queue_rag(db, co, case.id, t) == ("green", False)
        assert _page_rag(db, case, co, t) == "green"
    # 18th, 00:30 UAE: attendance not recorded -> overdue and the ladder runs (critical: supervisor + clinical lead)
    t = day_end + timedelta(minutes=30)
    run_sweep(db, now=t, case_ids=[case.id])
    assert step.breached
    new = db.scalars(select(Escalation).where(Escalation.case_id == case.id).order_by(Escalation.id)).all()[n0:]
    assert sorted(e.level for e in new) == [2, 3] and {e.kind for e in new} == {"step"}
    assert _queue_rag(db, co, case.id, t) == ("red", True)
    assert _page_rag(db, case, co, t) == "red"
    db.rollback()


def test_reschedule_moves_the_deadline_and_attendance_moves_on(client, db, as_user, new_case):
    case = _accepted_past_target(client, db, as_user, new_case)
    co = _actor(db, "aisha.rahman")
    now = utcnow()
    start, _ = _visit(20)
    wf.book_appointment(db, case, co, start, beyond_target_reason="As suggested by doctor", now=now)
    new_start, new_end = _visit(40)
    wf.reschedule_appointment(db, case, co, new_start, "clinic_cancelled", note="clinic closed",
                              beyond_target_reason="As suggested by doctor", now=now + timedelta(hours=1))
    step = _step(db, case.id)
    assert step.state == "appointment_booked" and step.due_at == case.due_at == new_end
    assert run_sweep(db, now=new_end - timedelta(minutes=1), case_ids=[case.id])["breaches"] == 0
    wf.record_attendance(db, case, co, True, now=new_start + timedelta(hours=1))
    nxt = _step(db, case.id)
    assert case.state == "appointment_attended" and nxt.state == "appointment_attended"
    assert nxt.due_at is not None and nxt.due_at != new_end
    db.rollback()


def test_backfill_fixes_old_capped_deadlines_idempotently(client, db, as_user, new_case):
    case = _accepted_past_target(client, db, as_user, new_case)
    co = _actor(db, "aisha.rahman")
    now = utcnow()
    start, day_end = _visit()
    wf.book_appointment(db, case, co, start, beyond_target_reason="As suggested by doctor", now=now)
    step = _step(db, case.id)
    step.due_at = case.due_at = now  # as stored before this fix: capped at the (passed) target = overdue at once
    step.breached, case.escalation_level = True, 3
    db.add(Lesson(case_id=case.id, kind="overdue", reason_code="pathway_target_missed", created_at=now))
    db.flush()
    lessons = db.scalars(select(Lesson).where(Lesson.case_id == case.id)).all()
    n_esc = _n_esc(db, case.id)
    out = wf.backfill_booked_appointment_due(db, now=now)
    assert case.id in out["case_ids"]
    assert step.due_at == case.due_at == day_end and not step.breached and case.escalation_level == 0
    assert db.scalars(select(Lesson).where(Lesson.case_id == case.id)).all() == lessons  # history kept
    assert _n_esc(db, case.id) == n_esc
    assert case.id not in wf.backfill_booked_appointment_due(db, now=now).get("case_ids", [])  # idempotent
    assert _queue_rag(db, co, case.id, now + timedelta(minutes=1)) == ("green", False)
    db.rollback()


def test_alert_policy_waiting_argument():
    t0, target = datetime(2026, 11, 1), datetime(2026, 11, 5)
    wait = datetime(2027, 1, 17, 20)  # 18 Jan 00:00 UAE
    kw = dict(tier="critical", step_started=t0, step_due=wait, case_created=datetime(2026, 10, 1), target_due=target,
              care_reached=None, step_level_alerted=0, target_level_alerted=4)
    assert decide(now=datetime(2026, 12, 1), **kw).clock == "pathway"  # default: old behaviour (alert_eval)
    assert decide(now=datetime(2026, 12, 1), waiting_until=wait, **kw) == AlertDecision("waiting", 0, 0.0, (), False)
    assert decide(now=wait - timedelta(minutes=1), waiting_until=wait, **kw).new_levels == ()
    d = decide(now=wait + timedelta(minutes=30), waiting_until=wait, **kw)
    assert d.clock == "step" and d.breached and d.new_levels == (2, 3)
    d2 = decide(now=wait + timedelta(minutes=30), waiting_until=wait, **{**kw, "step_level_alerted": 3})
    assert d2.breached and d2.new_levels == ()
