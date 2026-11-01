"""Iteration 7 round 11 failure mode: consent withdrawn while a case is open.

Before: withdrawing care-coordination consent changed nothing (automated reminders kept going, no clinician was
told) and a 'no phone contact' decision still produced call tasks."""
import pytest
from sqlalchemy import select


def _patient_actor(p):
    from kareshafaa.security import Actor

    return Actor(user_id=None, role="patient", display="patient", patient_id=p.id)


def test_withdrawal_flags_open_case_and_stops_automated_messages(db, new_case):
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.models import Case
    from kareshafaa.security import SYSTEM
    from kareshafaa.services import workflow as wf
    from kareshafaa.services.errors import DomainError

    c = db.get(Case, new_case(value=250, smartphone=True)["id"])
    tier = c.tier
    wf.record_consent(db, c.patient, _patient_actor(c.patient), "care_coordination", "deny")
    db.flush()
    assert c.review_required and "consent_withdrawn" in c.review_reasons and c.tier == tier
    with pytest.raises(DomainError, match="withdrew consent"):
        send_with_ladder(db, c, "reminder", SYSTEM)
    db.rollback()


def test_clinician_may_still_contact_after_withdrawal(db, new_case):
    """Duty of care: a named clinician can still make contact (audited); only AUTOMATED outreach stops."""
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.models import Case, User
    from kareshafaa.services import workflow as wf

    c = db.get(Case, new_case(value=250, smartphone=True)["id"])
    wf.record_consent(db, c.patient, _patient_actor(c.patient), "care_coordination", "deny")
    co = actor_from_user(db.scalar(select(User).where(User.username == "aisha.rahman")))
    assert send_with_ladder(db, c, "result_notification", co)["attempts"]
    db.rollback()


def test_phone_consent_denied_creates_no_call_task(db, new_case):
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.models import CallTask, Case
    from kareshafaa.security import SYSTEM
    from kareshafaa.services import workflow as wf

    c = db.get(Case, new_case(value=250)["id"])  # no digital channels -> ladder ends at phone
    wf.record_consent(db, c.patient, _patient_actor(c.patient), "comm_phone", "deny")
    r = send_with_ladder(db, c, "result_notification", SYSTEM)
    assert r["attempts"][-1] == {"channel": "phone", "status": "not-done (no consent)"}
    assert db.scalars(select(CallTask).where(CallTask.case_id == c.id)).first() is None
    db.rollback()


def test_sweep_sends_no_reminder_after_withdrawal(db, new_case):
    from datetime import timedelta

    from kareshafaa.calendar_uae import to_local
    from kareshafaa.models import Case, Communication, StepTask
    from kareshafaa.services import workflow as wf
    from kareshafaa.services.escalation import run_sweep

    cid = new_case("diabetes_hba1c", 7.5, smartphone=True)["id"]
    c = db.get(Case, cid)
    step = db.scalar(select(StepTask).where(StepTask.case_id == cid, StepTask.status == "in-progress"))
    c.state = step.state = "patient_notified"
    wf.record_consent(db, c.patient, _patient_actor(c.patient), "care_coordination", "deny")
    sla_h = (step.due_at - step.started_at).total_seconds() / 3600
    cands = [step.started_at + timedelta(minutes=m) for m in range(int(sla_h * 6), int(sla_h * 60), 10)]
    # a working-hours time that the alert policy itself puts at reminder level 1 (the SLA clock pauses at
    # weekends, so a wall-clock fraction alone fails when the case opens on a Saturday or Sunday)
    from kareshafaa.services.escalation import required_level
    from kareshafaa.sla import SlaLookup
    sla = SlaLookup.load(db)
    day = next(t for t in cands if 10 <= to_local(t).hour < 18 and required_level(c, step, t, sla)[0] == 1)
    out = run_sweep(db, now=day, case_ids=[cid])
    assert out["reminders"] == 1  # the staff-facing L1 still fires
    sent = db.scalars(select(Communication).where(Communication.case_id == cid,
                                                  Communication.category == "reminder")).all()
    assert sent == []
    db.rollback()
