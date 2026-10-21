"""Iteration 7 round 13 failure mode: a patient dies while a case is open.

Before: there was no way to record a death after registration, so outreach (reminders, call tasks) carried on; and a
case could be closed with reason 'deceased' for a patient NOT recorded as deceased (a mis-click silently ended the
follow-up of a living patient, while their other programmes kept messaging them)."""
from sqlalchemy import select

LEAD = "layla.al.hashimi"


def test_close_as_deceased_requires_recorded_death(client, as_user, new_case):
    cid = new_case(value=250)["id"]
    r = client.post(f"/api/cases/{cid}/close", headers=as_user(LEAD),
                    json={"closure_type": "authorised_decision", "reason": "deceased", "note": "family phoned to say"})
    assert r.status_code in (400, 409, 422) and "record" in r.text


def test_record_death_stops_outreach_and_reviews_every_open_case(client, as_user, db, new_case):
    from wasl.models import CallTask, Case, Patient

    c1 = new_case(value=250)  # no digital channels -> the ladder ends in a phone task
    pid = db.get(Case, c1["id"]).patient_id
    r = client.post(f"/api/cases/{c1['id']}/notify", headers=as_user("aisha.rahman"),
                    json={"category": "result_notification"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalars(select(CallTask).where(CallTask.case_id == c1["id"], CallTask.status == "requested")).first()

    r = client.post(f"/api/cases/{c1['id']}/patient-deceased", headers=as_user(LEAD),
                    json={"note": "death registry notification 21/10"})
    assert r.status_code == 200, r.text
    assert r.json()["flagged_cases"] == [c1["id"]]
    db.expire_all()
    assert db.get(Patient, pid).deceased
    c = db.get(Case, c1["id"])
    assert c.review_required and "deceased" in c.review_reasons
    assert not db.scalars(select(CallTask).where(CallTask.case_id == c1["id"], CallTask.status == "requested")).first()
    # all messaging now refused, including by a human
    r = client.post(f"/api/cases/{c1['id']}/notify", headers=as_user("aisha.rahman"), json={"category": "reminder"})
    assert r.status_code in (400, 409, 422)
    # closing as deceased is now possible (authorised clinician decision)
    r = client.post(f"/api/cases/{c1['id']}/close", headers=as_user(LEAD),
                    json={"closure_type": "authorised_decision", "reason": "deceased",
                          "note": "death registry notification 21/10"})
    assert r.status_code == 200, r.text
    # recording twice is refused
    r = client.post(f"/api/cases/{c1['id']}/patient-deceased", headers=as_user(LEAD),
                    json={"note": "death registry notification again"})
    assert r.status_code in (400, 409, 422)


def test_record_death_needs_authorised_named_clinician(client, as_user, new_case):
    cid = new_case(value=250)["id"]
    assert client.post(f"/api/cases/{cid}/patient-deceased", headers=as_user("aisha.rahman"),
                       json={"note": "someone said so on the phone"}).status_code == 403  # coordinator
    assert client.post(f"/api/cases/{cid}/patient-deceased", headers=as_user(LEAD),
                       json={"note": "short"}).status_code in (400, 422)  # rationale >= 10 chars


def test_death_recorded_in_error_can_be_revoked_by_lead_only(client, as_user, db, new_case):
    from wasl.models import Case, Patient

    cid = new_case(value=250)["id"]
    h = as_user(LEAD)
    assert client.post(f"/api/cases/{cid}/patient-deceased", headers=h, json={"note": "death registry notice"}).status_code == 200
    assert client.post(f"/api/cases/{cid}/close", headers=h, json={"closure_type": "authorised_decision", "reason": "deceased",
                                                                   "note": "death registry notice"}).status_code == 200
    bad = client.post(f"/api/cases/{cid}/patient-deceased/revoke", headers=as_user("aisha.rahman"),
                      json={"note": "wrong patient selected, the registry entry was for another person"})
    assert bad.status_code == 403
    assert client.post(f"/api/cases/{cid}/patient-deceased/revoke", headers=h,
                       json={"note": "too short"}).status_code in (400, 422)
    r = client.post(f"/api/cases/{cid}/patient-deceased/revoke", headers=h,
                    json={"note": "wrong patient selected, the registry entry was for another person"})
    assert r.status_code == 200, r.text
    assert r.json()["closed_as_deceased"] == [cid]
    db.expire_all()
    assert not db.get(Patient, db.get(Case, cid).patient_id).deceased


def test_reopen_gets_fresh_target_and_is_refused_when_another_episode_is_open(db, new_case):
    """Round 20 (edge cohort): a re-opened case kept its original, long-past pathway target (born breached, alerts
    straight to the director); re-opening while a newer episode was open failed with an unexplained IntegrityError."""
    from datetime import timedelta

    import pytest

    from wasl.models import Case, User, utcnow
    from wasl.api.deps import actor_from_user
    from wasl.services import workflow as wf
    from wasl.services.errors import Invalid

    lead = actor_from_user(db.scalar(select(User).where(User.username == LEAD)))
    c = db.get(Case, new_case(value=250)["id"])
    long_ago = utcnow() - timedelta(days=200)
    c.created_at, c.target_due_at = long_ago, long_ago + timedelta(days=31)
    wf.close_case(db, c, lead, "authorised_decision", reason="clinically_not_indicated",
                  note="Closed after clinical review (test).")
    wf.transition(db, c, "triaged", lead, note="Re-opened: new symptoms reported (test).")
    assert c.target_due_at > utcnow() + timedelta(days=1)
    wf.close_case(db, c, lead, "authorised_decision", reason="clinically_not_indicated", note="Closed again (test).")
    _, c2 = wf.ingest_result(db, c.patient, c.programme, value=300, value_text=None, unit="ug/g", test="FIT",
                             performer_org_id=1)
    assert c2 is not None and c2.id != c.id
    with pytest.raises(Invalid, match="already open"):
        wf.transition(db, c, "triaged", lead, note="Re-opened again by mistake (test).")
    db.rollback()
