"""Critical-result fast lane (screening-to-care principle 5): direct clinician contact with read-back before
triage, phone escalation at the first breach, never closed as unreachable, visible on its own worklist."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from kareshafaa.models import Case, Communication, CriticalReadBack, Escalation, StepTask
from kareshafaa.services.escalation import run_sweep
from kareshafaa.statemachine import TransitionError, check_closure

LEAD = "fatima.al.mansoori"
GP = "omar.haddad"
COORD = "aisha.rahman"
RB = {"method": "phone", "reported_by": "Lab duty scientist", "read_back_confirmed": True}


def _critical(new_case):
    c = new_case("diabetes_hba1c", 16.0)
    assert c["tier"] == "critical" and c["state"] == "issued"
    return c


def test_triage_blocked_until_read_back(client, as_user, new_case, db):
    c = _critical(new_case)
    u = f"/api/cases/{c['id']}"
    r = client.post(f"{u}/transition", headers=as_user(LEAD), json={"to_state": "triaged"})
    assert r.status_code in (400, 409, 422) and "read-back" in r.text
    acts = {a["key"] for a in client.get(u, headers=as_user(LEAD)).json()["actions"]}
    assert "read_back" in acts and "triage" not in acts
    r = client.post(f"{u}/critical-read-back", headers=as_user(LEAD), json=RB)
    assert r.status_code == 200, r.text
    d = client.get(u, headers=as_user(LEAD)).json()
    assert d["fast_lane"]["read_back"]["method"] == "phone" and not d["fast_lane"]["read_back_needed"]
    assert "triage" in {a["key"] for a in d["actions"]}
    assert client.post(f"{u}/transition", headers=as_user(LEAD), json={"to_state": "triaged"}).status_code == 200


def test_read_back_must_be_confirmed_and_by_a_clinician(client, as_user, new_case):
    c = _critical(new_case)
    u = f"/api/cases/{c['id']}/critical-read-back"
    assert client.post(u, headers=as_user(LEAD), json={**RB, "read_back_confirmed": False}).status_code in (400, 422)
    assert client.post(u, headers=as_user(COORD), json=RB).status_code == 403  # coordinator: not a clinician
    assert client.post(u, headers=as_user("admin"), json=RB).status_code == 403  # admin: no clinical actions
    assert client.post(u, headers=as_user(GP), json={**RB, "reported_by": "x"}).status_code in (400, 422)


def test_read_back_only_for_critical(client, as_user, new_case):
    c = new_case()  # FIT 250: not critical
    r = client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user(LEAD), json=RB)
    assert r.status_code in (400, 422)


def test_audit_detail_is_pseudonymous(client, as_user, new_case, db):
    from kareshafaa.models import AuditEvent
    c = _critical(new_case)
    client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user(LEAD), json=RB)
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.case_id == c["id"],
                                            AuditEvent.event == "critical.read_back_recorded"))
    assert ev is not None and set(ev.detail) == {"method", "received_by_practitioner_id", "contacted_at"}


def test_worklist_shows_critical_without_read_back(client, as_user, new_case):
    c = _critical(new_case)
    q = client.get("/api/queue?view=critical&limit=1000", headers=as_user(LEAD)).json()
    row = next(r for r in q["items"] if r["id"] == c["id"])
    assert row["read_back_needed"] and q["counts"]["critical"] >= 1
    client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user(LEAD), json=RB)
    q = client.get("/api/queue?view=critical&limit=1000", headers=as_user(LEAD)).json()
    assert c["id"] not in {r["id"] for r in q["items"]}


def test_first_breach_escalates_by_phone(db, new_case):
    c = _critical(new_case)
    db.expire_all()
    step = db.scalar(select(StepTask).where(StepTask.case_id == c["id"], StepTask.status == "in-progress"))
    run_sweep(db, now=step.started_at + timedelta(hours=step.sla_hours * 1.05), case_ids=[c["id"]])
    escs = db.scalars(select(Escalation).where(Escalation.case_id == c["id"])).all()
    assert {e.level for e in escs if e.channel == "phone"} >= {3}
    assert all(e.channel == "internal" for e in escs if e.level == 1)
    comm = db.scalars(select(Communication).where(Communication.case_id == c["id"],
                                                  Communication.category == "escalation")).all()
    assert any(m.channel == "phone" for m in comm)
    db.rollback()


def test_non_critical_breach_stays_internal(db, new_case):
    c = new_case()
    db.expire_all()
    step = db.scalar(select(StepTask).where(StepTask.case_id == c["id"], StepTask.status == "in-progress"))
    run_sweep(db, now=step.started_at + timedelta(hours=step.sla_hours * 1.6), case_ids=[c["id"]])
    escs = db.scalars(select(Escalation).where(Escalation.case_id == c["id"])).all()
    assert escs and all(e.channel == "internal" for e in escs)
    db.rollback()


def test_critical_never_closed_as_unreachable():
    with pytest.raises(TransitionError, match="critical"):
        check_closure("unreachable", "authorised_decision", evidence_type=None, reason="unable_to_contact",
                      note="tried every channel for days", actor_is_human=True, actor_can_authorise=True,
                      tier="critical")
    check_closure("unreachable", "authorised_decision", evidence_type=None, reason="unable_to_contact",
                  note="tried every channel for days", actor_is_human=True, actor_can_authorise=True, tier="urgent")


def test_closure_reason_list_hides_unreachable_for_critical(client, as_user, new_case):
    c = _critical(new_case)
    reasons = client.get(f"/api/cases/{c['id']}/actions", headers=as_user(LEAD)).json()["closure_reasons"]
    assert "unable_to_contact" not in reasons and "informed_decline" in reasons
    other = new_case()
    assert "unable_to_contact" in client.get(f"/api/cases/{other['id']}/actions", headers=as_user(LEAD)).json()["closure_reasons"]


def test_read_back_row_is_never_written_without_confirmation(db, new_case):
    from kareshafaa.security import Actor
    from kareshafaa.services.critical import record_read_back
    from kareshafaa.services.errors import Invalid
    c = _critical(new_case)
    case = db.get(Case, c["id"])
    lead = Actor(user_id=None, role="clinical_lead", display="Lead", practitioner_id=case.owner_id)
    with pytest.raises(Invalid):
        record_read_back(db, case, lead, method="phone", reported_by="Lab", read_back_confirmed=False)
    assert db.scalar(select(CriticalReadBack).where(CriticalReadBack.case_id == case.id)) is None
    db.rollback()


def test_contacted_at_utc_now_ok_and_unconverted_local_time_is_future(client, as_user, new_case):
    """The case page sends the contact time as UTC (local wall clock converted in the browser). A naive local
    IST time sent as if it were UTC looks ~5.5 h ahead and must be refused, not silently stored."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0)
    c = _critical(new_case)
    u = f"/api/cases/{c['id']}/critical-read-back"
    ahead = (now + timedelta(hours=5, minutes=30)).replace(tzinfo=None).isoformat()
    r = client.post(u, headers=as_user(LEAD), json={**RB, "contacted_at": ahead})
    assert r.status_code == 422 and "future" in r.text
    r = client.post(u, headers=as_user(LEAD), json={**RB, "contacted_at": now.isoformat().replace("+00:00", "Z")})
    assert r.status_code == 200, r.text
