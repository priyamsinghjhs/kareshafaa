"""Alert policy (Iteration 7): step clock vs consolidated pathway-target clock; no repeat alert per step after a
missed target, the ladder still escalates, and the first alert on any true breach is never suppressed."""
import random
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from wasl.alert_policy import Ladder, decide, ladder_level
from wasl.models import Case, Escalation, Lesson, StepTask
from wasl.security import SYSTEM
from wasl.services import workflow as wf
from wasl.services.escalation import run_sweep
from wasl.statemachine import S

T0 = datetime(2026, 1, 1)
L = Ladder()


@pytest.mark.parametrize("frac,tier,level", [
    (0.49, "high", 0), (0.5, "high", 1), (0.99, "high", 1), (1.0, "high", 2), (1.49, "high", 2), (1.5, "high", 3),
    (5.0, "high", 3), (5.0, "routine", 3), (1.99, "urgent", 3), (2.0, "urgent", 4), (1.0, "critical", 3),
    (2.0, "critical", 4)])
def test_ladder(frac, tier, level):
    assert ladder_level(frac, tier, L) == level


def _d(now, **kw):
    base = dict(tier="high", step_started=T0, step_due=T0 + timedelta(days=10), case_created=T0,
                target_due=T0 + timedelta(days=31), care_reached=None, now=now, step_level_alerted=0,
                target_level_alerted=0)
    base.update(kw)
    return decide(**base)


def test_step_clock_before_target():
    d = _d(T0 + timedelta(days=11))
    assert d.clock == "step" and d.new_levels == (1, 2) and d.breached


def test_target_missed_switches_to_pathway_clock_and_first_alert_is_never_suppressed():
    # step already alerted up to L4 on its own clock: the pathway breach is a NEW event and still alerts
    d = _d(T0 + timedelta(days=32), step_level_alerted=4)
    assert d.clock == "pathway" and d.new_levels == (2,)


def test_step_opened_after_missed_target_does_not_repeat_alerts_but_ladder_continues():
    start = T0 + timedelta(days=40)
    kw = dict(step_started=start, step_due=start, target_level_alerted=2)
    assert _d(start + timedelta(hours=1), **kw).new_levels == ()          # no repeat L2 for the new step
    assert _d(T0 + timedelta(days=47), **kw).new_levels == (3,)           # 1.52 x pathway window -> L3
    assert _d(T0 + timedelta(days=70), tier="urgent", **kw).new_levels == (3, 4)


def test_no_l1_on_pathway_clock():
    d = _d(T0 + timedelta(days=31, hours=1))
    assert 1 not in d.new_levels and d.new_levels == (2,)


def test_care_reached_stops_pathway_clock():
    d = _d(T0 + timedelta(days=40), care_reached=T0 + timedelta(days=35), step_started=T0 + timedelta(days=35),
           step_due=T0 + timedelta(days=45))
    assert d.clock == "step" and not d.breached


def test_random_timelines_one_alert_per_level_and_no_missed_breach():
    """Simulate 5-minute sweeps over random step sequences: per case, each pathway level is raised at most once,
    and every true breach (step deadline before the target, or the target itself) gets an L2+ alert within one
    sweep interval if it lasts that long."""
    rng = random.Random(11)
    for _ in range(120):
        tier = rng.choice(["critical", "urgent", "high", "routine"])
        target = T0 + timedelta(days=rng.choice([1, 7, 14, 31, 90]))
        t, step_alerted, target_alerted, raised = T0, 0, 0, []
        missed_true = []
        for _k in range(rng.randint(1, 6)):
            dur = timedelta(hours=rng.uniform(1, 24 * 40))
            sla = timedelta(hours=rng.uniform(4, 24 * 30))
            due = min(t + sla, max(target, t))
            end = t + dur
            step_alerted = 0
            first_breach_alert = None
            now = t
            while now < end:
                d = decide(tier=tier, step_started=t, step_due=due, case_created=T0, target_due=target,
                           care_reached=None, now=now, step_level_alerted=step_alerted,
                           target_level_alerted=target_alerted)
                for lv in d.new_levels:
                    raised.append((d.clock, lv, t if d.clock == "step" else None))
                    if lv >= 2 and first_breach_alert is None:
                        first_breach_alert = now
                if d.clock == "pathway":
                    target_alerted = max(target_alerted, d.level)
                else:
                    step_alerted = max(step_alerted, d.level)
                now += timedelta(minutes=5)
            deadline = due if t < target else None
            if deadline is not None and end - deadline > timedelta(minutes=5) and first_breach_alert is None:
                missed_true.append((t, deadline, end))
            t = end
        assert not missed_true
        pathway = [lv for c, lv, _ in raised if c == "pathway"]
        assert len(pathway) == len(set(pathway))
        steps = [(lv, s) for c, lv, s in raised if c == "step"]
        assert len(steps) == len(set(steps))


def _step(db, cid):
    return db.scalar(select(StepTask).where(StepTask.case_id == cid, StepTask.status == "in-progress"))


def test_sweep_consolidates_alerts_after_missed_target(db, new_case):
    cid = new_case("colorectal_fit", 250)["id"]
    db.expire_all()
    case, step = db.get(Case, cid), _step(db, cid)
    t0 = case.created_at
    case.target_due_at = t0 + timedelta(days=2)
    step.due_at = min(step.due_at, case.target_due_at)
    run_sweep(db, now=case.target_due_at + timedelta(hours=1), case_ids=[cid])
    esc = db.scalars(select(Escalation).where(Escalation.case_id == cid, Escalation.kind == "pathway")).all()
    assert [e.level for e in esc] == [2] and case.target_alert_level == 2
    # a new step opens after the missed target: it is born breached, gets no repeat L2, inherits the ladder
    now = case.target_due_at + timedelta(hours=2)
    wf._close_step(db, case, now)
    wf._open_step(db, case, S.PATIENT_NOTIFIED, case.owner_id, SYSTEM, now)
    new = _step(db, cid)
    assert new.inherited_breach and new.breached
    run_sweep(db, now=now + timedelta(hours=1), case_ids=[cid])
    assert len(db.scalars(select(Escalation).where(Escalation.case_id == cid, Escalation.level >= 2)).all()) == 1
    run_sweep(db, now=t0 + timedelta(days=3.1), case_ids=[cid])  # 1.55 x the pathway window
    levels = sorted(e.level for e in db.scalars(select(Escalation).where(Escalation.case_id == cid,
                                                                        Escalation.level >= 2)))
    assert levels == [2, 3]
    wf._close_step(db, case, t0 + timedelta(days=3.2))  # inherited step closing late: no extra overdue lesson
    lessons = db.scalars(select(Lesson).where(Lesson.case_id == cid, Lesson.kind == "overdue")).all()
    assert len(lessons) == 1
    db.rollback()
