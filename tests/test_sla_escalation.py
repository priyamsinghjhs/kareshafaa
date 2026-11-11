"""SLA timers, escalation ladder, work queue and auto-pooling."""
from datetime import timedelta

from sqlalchemy import select

from kareshafaa.models import Case, Escalation, Practitioner, StepTask
from kareshafaa.security import SYSTEM
from kareshafaa.services.escalation import run_sweep


def _step(db, cid):
    return db.scalar(select(StepTask).where(StepTask.case_id == cid, StepTask.status == "in-progress"))


def test_escalation_ladder_levels(db, new_case):
    cid = new_case("diabetes_hba1c", 7.5)["id"]  # routine, triaged
    db.expire_all()
    case, step = db.get(Case, cid), _step(db, cid)
    t0 = step.started_at
    sla_h = (step.due_at - t0).total_seconds() / 3600  # T3 clocks count working days: use the wall-clock window
    out = run_sweep(db, now=t0 + timedelta(hours=sla_h * 0.3), case_ids=[cid])
    assert out["reminders"] == 0 and case.escalation_level == 0
    run_sweep(db, now=t0 + timedelta(hours=sla_h * 0.6), case_ids=[cid])
    assert case.escalation_level == 1
    run_sweep(db, now=t0 + timedelta(hours=sla_h * 1.1), case_ids=[cid])
    assert case.escalation_level == 2 and step.breached
    run_sweep(db, now=t0 + timedelta(hours=sla_h * 1.6), case_ids=[cid])
    assert case.escalation_level == 3
    levels = sorted(e.level for e in db.scalars(select(Escalation).where(Escalation.case_id == cid)))
    assert levels == [1, 2, 3]
    # idempotent: re-running does not duplicate escalations
    run_sweep(db, now=t0 + timedelta(hours=sla_h * 1.7), case_ids=[cid])
    assert len(db.scalars(select(Escalation).where(Escalation.case_id == cid)).all()) == 3
    run_sweep(db, now=t0 + timedelta(hours=sla_h * 2.5), case_ids=[cid])
    assert case.escalation_level == 3  # T3 never reaches L4: it goes to the weekly review list
    lead = db.get(Practitioner, db.scalar(select(Escalation.target_practitioner_id).where(
        Escalation.case_id == cid, Escalation.level == 3)))
    assert lead.role == "clinical_lead"
    db.commit()


def test_critical_breach_goes_straight_to_clinical_lead(db, new_case, client, as_user):
    c = new_case("diabetes_hba1c", 16.0)
    assert c["tier"] == "critical" and c["state"] == "issued"
    assert client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user("fatima.al.mansoori"),
                       json={"method": "phone", "reported_by": "Lab duty scientist", "read_back_confirmed": True}).status_code == 200
    assert client.post(f"/api/cases/{c['id']}/transition", headers=as_user("fatima.al.mansoori"),
                       json={"to_state": "triaged"}).status_code == 200
    db.expire_all()
    step = _step(db, c["id"])
    run_sweep(db, now=step.started_at + timedelta(hours=step.sla_hours * 1.05), case_ids=[c["id"]])
    assert db.get(Case, c["id"]).escalation_level == 3
    run_sweep(db, now=step.started_at + timedelta(hours=step.sla_hours * 2.1), case_ids=[c["id"]])
    assert db.get(Case, c["id"]).escalation_level == 4  # L4 medical director for T0/T1
    target = db.scalar(select(Escalation.target_practitioner_id).where(Escalation.case_id == c["id"], Escalation.level == 4))
    assert db.get(Practitioner, target).programme is None
    db.commit()


def test_inactive_owner_auto_pooled(db, new_case):
    cid = new_case()["id"]
    db.expire_all()
    case = db.get(Case, cid)
    owner = db.get(Practitioner, case.owner_id)
    owner_active = owner.active
    owner.active = False
    out = run_sweep(db, case_ids=[cid])
    assert out["auto_pooled"] == 1
    assert db.get(Practitioner, case.owner_id).is_pool
    assert _step(db, cid).owner_id == case.owner_id
    db.rollback()
    assert owner_active


def test_queue_views_and_order(client, as_user):
    h = as_user("aisha.rahman")
    q = client.get("/api/queue?view=all&limit=500", headers=h).json()
    assert q["counts"]["all"] > 0 and q["items"]
    # Default order is urgency first, then overdue status within a tier — a
    # green critical case can sit above a red routine one.
    rank = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}
    sla = {"red": 0, "amber": 1, "green": 2}
    tiers = [rank.get(i["tier"], 9) for i in q["items"]]
    assert tiers == sorted(tiers)
    from collections import defaultdict
    by_tier = defaultdict(list)
    for i in q["items"]:
        by_tier[i["tier"]].append(sla.get(i["sla_status"], 3))
    for keys in by_tier.values():
        assert keys == sorted(keys)
    for view in ("overdue", "unassigned", "rejected", "escalated", "calls", "transfers", "mine"):
        r = client.get(f"/api/queue?view={view}", headers=h)
        assert r.status_code == 200, view
    over = client.get("/api/queue?view=overdue", headers=h).json()["items"]
    assert all(i["sla_status"] == "red" for i in over)
    un = client.get("/api/queue?view=unassigned", headers=h).json()["items"]
    assert all(i["owner_is_pool"] for i in un)


def test_sweep_endpoint_requires_permission(client, as_user):
    assert client.post("/api/sla/sweep", headers=as_user("analyst"), json={}).status_code == 403
    r = client.post("/api/sla/sweep", headers=as_user("aisha.rahman"), json={})
    assert r.status_code == 200 and "checked" in r.json()


def test_escalation_ack(client, as_user, db):
    db.expire_all()
    esc = db.scalar(select(Escalation).where(Escalation.acknowledged_at.is_(None), Escalation.level == 3))
    h = as_user("fatima.al.mansoori")
    r = client.post(f"/api/escalations/{esc.id}/ack", headers=h, json={"note": "called the PHC lead"})
    assert r.status_code == 200
    assert client.post(f"/api/escalations/{esc.id}/ack", headers=h, json={"note": "again"}).status_code == 409


def test_system_cannot_triage_urgent(db, new_case):
    import pytest

    from kareshafaa.services import workflow as wf
    from kareshafaa.services.errors import Forbidden

    cid = new_case("breast", None, "BI-RADS 5")["id"]
    case = db.get(Case, cid)
    assert case.tier == "urgent"
    with pytest.raises(Forbidden):
        wf.transition(db, case, "triaged", SYSTEM)
    db.rollback()


def test_working_day_calendar():
    from datetime import datetime

    from kareshafaa.calendar_uae import add_working_hours, is_working_day, next_working_slot
    from kareshafaa.sla import due_at

    fri = datetime(2026, 10, 23, 10, 0)  # Friday
    assert is_working_day(fri.date()) and not is_working_day(datetime(2026, 10, 24).date())  # Sat weekend
    assert add_working_hours(fri, 24) == datetime(2026, 10, 26, 10, 0)  # skips Sat+Sun
    assert due_at(fri, 24, "routine") == datetime(2026, 10, 26, 10, 0)
    assert due_at(fri, 24, "urgent") == datetime(2026, 10, 24, 10, 0)  # T1 clocks run 24/7
    assert not is_working_day(datetime(2026, 12, 2).date())  # National Day
    assert next_working_slot(datetime(2026, 12, 1, 17, 0)).date() == datetime(2026, 12, 4).date()


def test_pathway_specific_confirmation():
    from kareshafaa.programmes import needs_confirmation, triage_tier

    assert not needs_confirmation("cervical", None, "HPV16") and needs_confirmation("cervical", None, "HPV other hr")
    assert not needs_confirmation("breast", None, "BI-RADS 4") and needs_confirmation("breast", None, "BI-RADS 0")
    assert not needs_confirmation("colorectal_fit", 300, None)
    assert triage_tier("diabetes_hba1c", 15.2, None) == "critical" and triage_tier("breast", None, "BI-RADS 5") == "urgent"


def test_patient_reminders_respect_uae_message_window(db, new_case):
    """Automated L1 patient reminders are never sent between 21:00 and 08:00 UAE time: they are queued and sent
    once by the first sweep inside the window (failure mode found in Iteration 7: 2 a.m. SMS from the SLA job)."""
    from datetime import datetime

    from kareshafaa.calendar_uae import to_utc
    from kareshafaa.models import Communication
    from kareshafaa.services.escalation import in_patient_window

    assert not in_patient_window(to_utc(datetime(2026, 10, 21, 2, 0)))
    assert in_patient_window(to_utc(datetime(2026, 10, 21, 8, 0)))
    assert not in_patient_window(to_utc(datetime(2026, 10, 21, 21, 0)))
    cid = new_case("diabetes_hba1c", 7.5)["id"]
    db.expire_all()
    case, step = db.get(Case, cid), _step(db, cid)
    case.state = "patient_notified"  # a patient-facing step (test shortcut)
    step.state = "patient_notified"
    t0 = step.started_at
    sla_h = (step.due_at - t0).total_seconds() / 3600
    # choose an L1 moment (50-95 % of the SLA) at 02:00 UAE time
    from kareshafaa.calendar_uae import to_local
    cands = [t0 + timedelta(minutes=m) for m in range(int(sla_h * 30), int(sla_h * 57), 10)]
    night = next(t for t in cands if to_local(t).hour == 2)
    assert night < step.due_at

    def reminders(status=None):
        q = select(Communication).where(Communication.case_id == cid, Communication.category == "reminder")
        return [c for c in db.scalars(q) if status is None or c.status == status]

    out = run_sweep(db, now=night, case_ids=[cid])
    assert out["reminders"] == 1 and out.get("reminders_deferred") == 1
    assert [c.channel for c in reminders()] == ["scheduled"]
    out = run_sweep(db, now=night + timedelta(hours=1), case_ids=[cid])  # still night: nothing sent
    assert len(reminders()) == 1
    morning = to_utc(to_local(night).replace(hour=8, minute=5))
    out = run_sweep(db, now=morning, case_ids=[cid])
    assert out["reminders_sent_after_deferral"] == 1
    sent = [c for c in reminders() if c.channel != "scheduled"]
    assert sent and all(c.sent_at == morning for c in sent)
    run_sweep(db, now=morning + timedelta(minutes=5), case_ids=[cid])  # sent exactly once
    assert len([c for c in reminders() if c.channel != "scheduled"]) == len(sent)
    db.rollback()
