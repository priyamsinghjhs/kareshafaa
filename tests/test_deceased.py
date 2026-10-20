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
