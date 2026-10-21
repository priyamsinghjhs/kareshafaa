"""Round 19: human sign-off audit of high-impact action paths.

Gaps found (each test fails on the round-18 code):
1. Consent decisions recorded by staff ON THE PATIENT'S BEHALF (a withdrawal stops automated outreach in every
   programme) and declines recorded by staff (the pathway stops) only checked a permission: an automated job holding
   a coordinator role could do either, and no rationale was needed.
2. Evidence from a CLOSED episode was accepted after the case was re-opened: an old care plan let a generic
   transition mark treatment started without the careplan.start sign-off, and an old negative confirmatory result or
   fulfilled appointment could close the re-opened case without the new result.
"""
import pytest
from sqlalchemy import func, select

from wasl.models import AuditEvent, CarePlan, Case, Consent, User
from wasl.security import Actor
from wasl.services.errors import Forbidden, Invalid
from wasl.statemachine import TransitionError

BOT = Actor(user_id=7, role="coordinator", display="integration job with a coordinator role", is_human=False)


def _user(db, username):
    from wasl.api.deps import actor_from_user

    return actor_from_user(db.scalar(select(User).where(User.username == username)))


def _lead(db):
    return _user(db, "fatima.al.mansoori")


def _patient_actor(p):
    return Actor(user_id=None, role="patient", display="patient", patient_id=p.id)


def _n_consents(db, pid):
    return db.scalar(select(func.count()).select_from(Consent).where(Consent.patient_id == pid))


# ------------------------------------------------------------------ 1. consent / decline on the patient's behalf
@pytest.mark.parametrize("scope", ["care_coordination", "comm_sms", "share_external"])
def test_bot_cannot_record_consent_on_patients_behalf(db, new_case, scope):
    from wasl.services import workflow as wf

    c = db.get(Case, new_case(value=250)["id"])
    n0 = _n_consents(db, c.patient_id)
    with pytest.raises(Forbidden, match="named person"):
        wf.record_consent(db, c.patient, BOT, scope, "deny", "patient said so on the phone")
    assert _n_consents(db, c.patient_id) == n0 and not c.review_required
    db.rollback()


def test_staff_consent_needs_rationale_and_is_attributed(db, new_case):
    from wasl.services import workflow as wf

    c = db.get(Case, new_case(value=250)["id"])
    co = _user(db, "aisha.rahman")
    for note in (None, "", "by phone"):
        with pytest.raises(Invalid, match="rationale"):
            wf.record_consent(db, c.patient, co, "care_coordination", "deny", note)
    cons = wf.record_consent(db, c.patient, co, "care_coordination", "deny", "Patient phoned and withdrew consent")
    assert cons.recorded_by_user_id == co.user_id and c.review_required
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "consent.recorded", AuditEvent.entity_id == str(cons.id)))
    assert ev.actor_user_id == co.user_id
    db.rollback()


def test_patient_own_consent_decision_needs_no_staff_signoff(db, new_case):
    from wasl.services import workflow as wf

    c = db.get(Case, new_case(value=250)["id"])
    wf.record_consent(db, c.patient, _patient_actor(c.patient), "comm_sms", "deny")  # the patient's own choice
    db.rollback()


def test_api_staff_consent_without_rationale_refused(client, as_user, new_case):
    cid = new_case(value=250)["id"]
    co = as_user("aisha.rahman")
    r = client.post(f"/api/cases/{cid}/consent", headers=co, json={"scope": "comm_sms", "decision": "deny"})
    assert r.status_code in (400, 422), r.text
    r = client.post(f"/api/cases/{cid}/consent", headers=co,
                    json={"scope": "comm_sms", "decision": "deny", "note": "Patient asked by phone: no SMS please"})
    assert r.status_code == 200, r.text


def test_bot_cannot_record_decline_and_staff_needs_rationale(db):
    from wasl.services import workflow as wf

    c = db.scalar(select(Case).where(Case.state == "patient_notified", Case.review_required.is_(False)).limit(1))
    assert c is not None
    with pytest.raises(Forbidden, match="named person"):
        wf.record_decline(db, c, BOT, "Patient does not want the colonoscopy")
    with pytest.raises(Invalid, match="rationale"):
        wf.record_decline(db, c, _user(db, "aisha.rahman"), "no")
    assert c.state == "patient_notified"
    wf.record_decline(db, c, _user(db, "aisha.rahman"), "Patient does not want the colonoscopy now")
    assert c.state == "declined_pending_review"
    db.rollback()


def test_patient_own_decline_still_allowed(db):
    from wasl.services import workflow as wf

    c = db.scalar(select(Case).where(Case.state == "patient_notified", Case.review_required.is_(False)).limit(1))
    wf.record_decline(db, c, _patient_actor(c.patient), "not now")
    assert c.state == "declined_pending_review"
    db.rollback()


# ------------------------------------------------------------------ 2. stale evidence after a re-open
def _reopen(db, c, lead):
    from wasl.services import workflow as wf

    wf.transition(db, c, "triaged", lead, note="re-opened: new information from the hospital")
    for dst in ("patient_notified", "acknowledged"):
        wf.transition(db, c, dst, lead, note="progress (evidence of the earlier episode)")


def test_reopened_case_cannot_start_treatment_on_the_old_care_plan(db):
    from wasl.services import workflow as wf

    c = db.scalar(select(Case).join(CarePlan, CarePlan.case_id == Case.id).where(
        Case.state == "closed", CarePlan.status == "active", Case.needs_confirmation.is_(False)).limit(1))
    lead = _lead(db)
    _reopen(db, c, lead)
    wf.transition(db, c, "consultation", lead, note="consultation")
    with pytest.raises(TransitionError, match="since the last re-open"):
        wf.transition(db, c, "treatment_or_surveillance_started", lead, note="old plan")
    with pytest.raises(TransitionError):
        wf.close_case(db, c, lead, "evidence", evidence_type="care_plan")
    # the signed-off path still works: a NEW care plan for this episode
    wf.start_care(db, c, lead, "treatment", "New treatment plan after re-assessment")
    assert c.state == "treatment_or_surveillance_started"
    wf.close_case(db, c, lead, "evidence", evidence_type="care_plan")
    assert c.state == "closed"
    db.rollback()


def test_reopened_case_cannot_close_on_the_old_negative_confirmatory_result(db):
    from wasl.services import workflow as wf

    c = db.scalar(select(Case).where(Case.state == "closed",
                                     Case.closure_evidence_type == "negative_confirmatory_result").limit(1))
    if c is None:
        pytest.skip("test seed has no case closed on a negative confirmatory result")
    lead = _lead(db)
    _reopen(db, c, lead)
    wf.order_confirmatory(db, c, lead, performer_org_id=c.owner.org_id or 1)
    assert c.state == "confirmatory_test"
    with pytest.raises(TransitionError, match="since it was re-opened"):
        wf.close_case(db, c, lead, "evidence", evidence_type="negative_confirmatory_result")
    with pytest.raises(TransitionError):
        wf.start_care(db, c, lead, "surveillance", "surveillance on the old result")
    wf.record_confirmatory_result(db, c, lead, "negative")
    wf.close_case(db, c, lead, "evidence", evidence_type="negative_confirmatory_result")
    assert c.state == "closed"
    db.rollback()


def test_reopened_case_cannot_be_marked_attended_on_the_old_appointment(db):
    from datetime import timedelta

    from wasl.models import Appointment, ServiceRequest, utcnow
    from wasl.services import workflow as wf

    c = db.scalar(select(Case).join(Appointment, Appointment.case_id == Case.id).where(
        Case.state == "closed", Appointment.status == "fulfilled", Case.needs_confirmation.is_(False)).limit(1))
    lead = _lead(db)
    _reopen(db, c, lead)
    wf.transition(db, c, "consultation", lead, note="consultation")
    old_ref = db.scalar(select(ServiceRequest).where(ServiceRequest.case_id == c.id, ServiceRequest.kind == "referral")
                        .order_by(ServiceRequest.id.desc()).limit(1))
    wf.create_referral(db, c, lead, old_ref.performer_org_id, summary="Re-referral after re-opening: " + "x" * 60)
    wf.decide_referral(db, c, lead, True, note="accepted for re-assessment")
    wf.book_appointment(db, c, lead, utcnow() + timedelta(days=2), beyond_target_reason="earliest slot after re-opening")
    assert c.state == "appointment_booked"
    with pytest.raises(TransitionError, match="since the last re-open"):
        wf.transition(db, c, "appointment_attended", lead, note="old appointment")
    db.rollback()
