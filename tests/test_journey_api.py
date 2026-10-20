"""API integration: full closed-loop journey, closure guard, one-owner invariant, exceptions."""
from datetime import timedelta

from sqlalchemy import func, select

from wasl.models import Case, StepTask, utcnow

CORRECT = {"q1": "a", "q2": "b", "q3": "a"}
SUMMARY = "FIT 250 ug/g, no red flags, fit for colonoscopy; anticoagulants: none. Synthetic."


def _post(client, h, url, **json):
    return client.post(url, headers=h, json=json)


def _active_steps(db, cid):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(StepTask).where(StepTask.case_id == cid,
                                                                       StepTask.status == "in-progress"))


def notify_and_ack(client, as_user, cid):
    co = as_user("aisha.rahman")
    r = _post(client, co, f"/api/cases/{cid}/notify", category="result_notification")
    assert r.status_code == 200, r.text
    att = r.json()["result"]["attempts"]
    assert att[-1]["channel"] == "phone" and att[-1]["status"] == "call_task"  # no digital channel works
    assert all(not a.get("status") == "completed" for a in att)
    r = _post(client, co, f"/api/cases/{cid}/call", outcome="reached", note="explained result")
    assert r.json()["case"]["state"] == "patient_notified"
    r = _post(client, co, f"/api/cases/{cid}/teach-back", answers=CORRECT, confidence=4, language="en")
    assert r.json()["passed"] and r.json()["case"]["state"] == "acknowledged"


def test_full_colorectal_loop_closes_only_with_evidence(client, as_user, new_case, db):
    c = new_case("colorectal_fit", 250)
    cid = c["id"]
    assert c["state"] == "triaged" and c["tier"] == "high"  # deterministic rule auto-triage
    co, gp, sp, lead = (as_user(u) for u in ("aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori"))

    # closure guard: no evidence yet
    r = _post(client, gp, f"/api/cases/{cid}/close", closure_type="evidence", evidence_type="appointment_fulfilled")
    assert r.status_code == 409
    notify_and_ack(client, as_user, cid)
    assert _active_steps(db, cid) == 1
    r = _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed")
    assert r.status_code == 200, r.text
    r = _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, performer_id=31, summary=SUMMARY)
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "referral_sent"
    # GP cannot accept their own referral
    assert _post(client, gp, f"/api/cases/{cid}/referral/decision", accept=True).status_code == 403
    r = _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="accepted")
    assert r.json()["case"]["state"] == "referral_accepted"
    start = (utcnow() - timedelta(hours=1)).isoformat()  # attendance is recorded after the appointment time
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=start)
    assert r.json()["case"]["state"] == "appointment_booked"
    # still cannot close: appointment booked is not evidence
    r = _post(client, sp, f"/api/cases/{cid}/close", closure_type="evidence", evidence_type="appointment_fulfilled")
    assert r.status_code == 409
    r = _post(client, sp, f"/api/cases/{cid}/attendance", attended=True)
    assert r.json()["case"]["state"] == "appointment_attended"
    r = _post(client, sp, f"/api/cases/{cid}/care-plan", kind="surveillance", description="Colonoscopy done; 3y surveillance")
    assert r.json()["case"]["state"] == "treatment_or_surveillance_started"
    # coordinator (no closure:authorise) can close with evidence; evidence is verified server-side
    r = _post(client, sp, f"/api/cases/{cid}/close", closure_type="evidence", evidence_type="care_plan")
    assert r.status_code == 200, r.text
    assert r.json()["case"]["state"] == "closed"
    assert _active_steps(db, cid) == 0
    detail = client.get(f"/api/cases/{cid}", headers=lead).json()
    states = [s["state"] for s in detail["steps"]] if "steps" in detail else None
    assert detail["state"] == "closed"
    if states:
        assert states[0] == "issued" and states[-1] == "closed"
    # re-open requires case:reopen + a reason
    assert _post(client, sp, f"/api/cases/{cid}/reopen", note="patient symptomatic again").status_code == 403
    r = _post(client, lead, f"/api/cases/{cid}/reopen", note="New symptoms reported by GP")
    assert r.status_code == 200 and r.json()["case"]["state"] == "triaged"


def test_one_accountable_owner_always(db, seeded):
    db.expire_all()
    multi = db.execute(select(StepTask.case_id, func.count()).where(StepTask.status == "in-progress")
                       .group_by(StepTask.case_id).having(func.count() > 1)).all()
    assert multi == []
    open_cases = db.scalars(select(Case).where(Case.state != "closed")).all()
    assert open_cases and all(c.owner_id is not None for c in open_cases)
    for c in open_cases[:200]:
        step = db.scalar(select(StepTask).where(StepTask.case_id == c.id, StepTask.status == "in-progress"))
        assert step is not None and step.owner_id == c.owner_id and step.state == c.state


def test_authorised_closure_needs_permission_reason_and_note(client, as_user, new_case):
    cid = new_case("diabetes_hba1c", 7.2)["id"]
    co, gp = as_user("aisha.rahman"), as_user("omar.haddad")
    body = dict(closure_type="authorised_decision", reason="moved_abroad", note="Moved to Kerala; summary emailed.")
    assert _post(client, co, f"/api/cases/{cid}/close", **body).status_code == 403  # coordinator can't authorise
    assert _post(client, gp, f"/api/cases/{cid}/close", **{**body, "note": "moved"}).status_code in (409, 422)
    assert _post(client, gp, f"/api/cases/{cid}/close", **{**body, "reason": "made_up"}).status_code == 409
    # informed_decline requires a documented decline Consent
    r = _post(client, gp, f"/api/cases/{cid}/close", closure_type="authorised_decision", reason="informed_decline",
              note="Patient said no on the phone")
    assert r.status_code == 409
    # unable_to_contact requires the contact ladder to be exhausted
    r = _post(client, gp, f"/api/cases/{cid}/close", closure_type="authorised_decision", reason="unable_to_contact",
              note="Could not reach patient")
    assert r.status_code == 409
    r = _post(client, gp, f"/api/cases/{cid}/close", **body)
    assert r.status_code == 200 and r.json()["case"]["state"] == "closed"


def test_system_actor_cannot_close_and_risk_score_is_irrelevant(db, new_case):
    import pytest

    from wasl.security import ML_SERVICE, SYSTEM
    from wasl.services import workflow as wf
    from wasl.services.errors import Forbidden
    from wasl.statemachine import TransitionError

    cid = new_case()["id"]
    case = db.get(Case, cid)
    case.risk_score = 0.0001  # a very low risk score must never close or skip a step
    for actor in (SYSTEM, ML_SERVICE):
        with pytest.raises((TransitionError, Forbidden)):  # refused by the human sign-off guard (Iteration 7)
            wf.close_case(db, case, actor, "authorised_decision", reason="clinically_not_indicated",
                          note="model says low risk, close it")
    with pytest.raises(Exception):
        wf.transition(db, case, "referral_sent", ML_SERVICE)
    db.rollback()


def test_urgent_case_requires_clinician_triage(client, as_user, new_case):
    c = new_case("colorectal_fit", 600)
    assert c["tier"] == "urgent" and c["state"] == "issued"
    cid = c["id"]
    r = _post(client, as_user("aisha.rahman"), f"/api/cases/{cid}/transition", to_state="triaged")
    assert r.status_code == 403
    r = _post(client, as_user("fatima.al.mansoori"), f"/api/cases/{cid}/transition", to_state="triaged",
              note="urgent FIT confirmed")
    assert r.status_code == 200 and r.json()["case"]["state"] == "triaged"


def test_guard_blocks_skipping_evidence(client, as_user, new_case):
    cid = new_case()["id"]
    r = _post(client, as_user("aisha.rahman"), f"/api/cases/{cid}/transition", to_state="patient_notified")
    assert r.status_code == 409 and "notification" in r.json()["detail"]


def test_rejected_referral_requires_reason_and_rerouting(client, as_user, new_case):
    cid = new_case()["id"]
    notify_and_ack(client, as_user, cid)
    gp, sp = as_user("omar.haddad"), as_user("vikram.mehta")
    _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation")
    _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, summary=SUMMARY)
    assert _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=False).status_code in (409, 422)
    r = _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=False, reason_code="capacity",
              note="no slots in 4 weeks")
    assert r.status_code == 200 and r.json()["case"]["state"] == "referral_rejected"
    q = client.get("/api/queue?view=rejected", headers=as_user("aisha.rahman")).json()
    assert any(i["id"] == cid for i in q["items"])
    r = _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=10, summary=SUMMARY)
    assert r.status_code == 200 and r.json()["case"]["state"] == "referral_sent"


def test_no_show_then_rebook(client, as_user, new_case):
    cid = new_case()["id"]
    notify_and_ack(client, as_user, cid)
    gp, sp, co = as_user("omar.haddad"), as_user("vikram.mehta"), as_user("aisha.rahman")
    _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation")
    _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, summary=SUMMARY)
    _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=True)
    start = (utcnow() - timedelta(hours=1)).isoformat()
    _post(client, co, f"/api/cases/{cid}/appointment", start=start)
    r = _post(client, co, f"/api/cases/{cid}/attendance", attended=False, barrier_code="transport")
    assert r.json()["case"]["state"] == "no_show"
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=start)
    assert r.json()["case"]["state"] == "appointment_booked"


def test_unreachable_after_ladder_then_close(client, as_user, new_case):
    cid = new_case()["id"]
    co, gp = as_user("aisha.rahman"), as_user("omar.haddad")
    _post(client, co, f"/api/cases/{cid}/notify", category="result_notification")
    assert _post(client, co, f"/api/cases/{cid}/transition", to_state="unreachable").status_code == 409
    _post(client, co, f"/api/cases/{cid}/call", outcome="no_answer")
    _post(client, co, f"/api/cases/{cid}/call", outcome="wrong_number")
    r = _post(client, co, f"/api/cases/{cid}/transition", to_state="unreachable", note="ladder exhausted")
    assert r.status_code == 200, r.text
    r = _post(client, gp, f"/api/cases/{cid}/close", closure_type="authorised_decision", reason="unable_to_contact",
              note="Two calls failed; GP letter sent (simulated).")
    assert r.status_code == 200 and r.json()["case"]["state"] == "closed"


def test_patient_decline_and_informed_decline_closure(client, as_user, new_case):
    cid = new_case()["id"]
    co, gp = as_user("aisha.rahman"), as_user("omar.haddad")
    _post(client, co, f"/api/cases/{cid}/notify", category="result_notification")
    _post(client, co, f"/api/cases/{cid}/call", outcome="reached")
    r = _post(client, co, f"/api/cases/{cid}/decline", note="Patient does not want colonoscopy now")
    assert r.json()["case"]["state"] == "declined_pending_review"
    r = _post(client, gp, f"/api/cases/{cid}/close", closure_type="authorised_decision", reason="informed_decline",
              note="Risks explained by GP; patient's informed choice documented.")
    assert r.status_code == 200, r.text


def test_two_key_transfer(client, as_user, new_case):
    cid = new_case()["id"]
    co = as_user("aisha.rahman")
    before = client.get(f"/api/cases/{cid}", headers=co).json()["owner_id"]
    r = _post(client, co, f"/api/cases/{cid}/transfer", to_owner_id=21, note="patient moved to Khalifa City")
    assert r.status_code == 200
    assert client.get(f"/api/cases/{cid}", headers=co).json()["owner_id"] == before  # still accountable
    # a third party (other PHC GP) cannot accept
    assert _post(client, as_user("imran.siddiqui"), f"/api/cases/{cid}/transfer/decision", accept=True).status_code == 403
    r = _post(client, as_user("yousef.al.marzouqi"), f"/api/cases/{cid}/transfer/decision", accept=True)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/cases/{cid}", headers=co).json()["owner_id"] == 21


def test_consult_request_and_response(client, as_user, new_case):
    cid = new_case()["id"]
    gp, sp = as_user("omar.haddad"), as_user("vikram.mehta")
    r = _post(client, gp, f"/api/cases/{cid}/consults", question="Is colonoscopy safe on apixaban?", target_practitioner_id=31)
    assert r.status_code == 200, r.text
    con = r.json()["consult_id"]
    items = client.get("/api/consults", headers=sp).json()["items"]
    assert any(i["id"] == con for i in items)
    r = _post(client, sp, f"/api/consults/{con}/respond", response="Hold apixaban 48h; bridging not needed.")
    assert r.status_code == 200


def test_validation_errors_are_422(client, as_user, new_case):
    cid = new_case()["id"]
    r = _post(client, as_user("aisha.rahman"), f"/api/cases/{cid}/close", closure_type="because")
    assert r.status_code == 422
    r = _post(client, as_user("aisha.rahman"), f"/api/cases/{cid}/transition", to_state="triaged", hack=1)
    assert r.status_code == 422


def test_booking_beyond_target_needs_reason_and_year_typo_refused(client, as_user, new_case, db):
    """Round 14: an appointment after the pathway target (or a year-typo date) used to be accepted silently; the
    breach only surfaced weeks later when the target alert fired."""
    from wasl.models import AuditEvent

    cid = new_case()["id"]
    notify_and_ack(client, as_user, cid)
    gp, sp, co = as_user("omar.haddad"), as_user("vikram.mehta"), as_user("aisha.rahman")
    _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation")
    _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, summary=SUMMARY)
    _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=True)
    typo = (utcnow() + timedelta(days=400)).isoformat()
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=typo)
    assert r.status_code in (400, 422) and "year typo" in r.text
    db.expire_all()
    late = (db.get(Case, cid).target_due_at + timedelta(days=3)).isoformat()
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=late)
    assert r.status_code in (400, 422) and "pathway target" in r.text
    r = _post(client, co, f"/api/cases/{cid}/appointment", start=late, beyond_target_reason="earliest available slot")
    assert r.status_code == 200, r.text
    ev = db.scalars(select(AuditEvent).where(AuditEvent.case_id == cid, AuditEvent.event == "appointment.booked")).all()
    assert ev[-1].detail["beyond_target"] and ev[-1].detail["beyond_target_reason"] == "earliest available slot"
