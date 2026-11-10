"""A recorded staff teach-back stays visible and is never offered again as a blank first-time check."""
from sqlalchemy import func, select

from kareshafaa.models import QuestionnaireResponse

CORRECT = {"q1": "a", "q2": "b", "q3": "a"}
WRONG = {"q1": "b", "q2": "b", "q3": "a"}
FIRST = "Check the patient understood (teach-back)"
AGAIN = "Try teach-back again"


def _notified(client, as_user, new_case):
    cid = new_case("colorectal_fit", 250)["id"]
    co = as_user("aisha.rahman")
    assert client.post(f"/api/cases/{cid}/notify", headers=co, json={"category": "result_notification"}).status_code == 200
    r = client.post(f"/api/cases/{cid}/call", headers=co, json={"outcome": "reached", "note": "explained"})
    assert r.json()["case"]["state"] == "patient_notified"
    return cid, co


def _tb(client, h, cid, answers):
    return client.post(f"/api/cases/{cid}/teach-back", headers=h, json={"answers": answers, "confidence": 3, "language": "en"})


def _action(client, h, cid):
    d = client.get(f"/api/cases/{cid}", headers=h).json()
    return d, next((a for a in d["actions"] if a["key"] == "teachback"), None)


def _count(db, cid):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(QuestionnaireResponse).where(QuestionnaireResponse.case_id == cid))


def test_first_check_uses_first_time_label(client, as_user, new_case):
    cid, co = _notified(client, as_user, new_case)
    _, a = _action(client, co, cid)
    assert a and a["label"] == FIRST and a["primary"] and "last_teach_back" not in a


def test_failed_check_is_saved_shown_and_offered_as_try_again(client, as_user, new_case, db):
    cid, co = _notified(client, as_user, new_case)
    r = _tb(client, co, cid, WRONG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is False and body["score"] == 2 and body["total"] == 3
    assert body["case"]["state"] == "patient_notified"
    d, a = _action(client, co, cid)
    assert a is not None and a["label"] == AGAIN and a["label"] != FIRST and a["primary"]
    last = a["last_teach_back"]
    assert last["passed"] is False and last["score"] == 2 and last["total"] == 3 and last["mode"] == "navigator"
    assert last["at"] and last["attempts"] == 1
    assert len(d["teach_back"]) == 1 and d["teach_back"][0]["answers_text"][0]["question"]
    # the queue/overview label comes from the same list
    acts = client.get(f"/api/cases/{cid}/actions", headers=co).json()["actions"]
    assert [x["label"] for x in acts if x["key"] == "teachback"] == [AGAIN]

    # a second failed attempt is allowed and both are kept
    r = _tb(client, co, cid, {"q1": "a", "q2": "a", "q3": "a"})
    assert r.status_code == 200 and r.json()["passed"] is False
    assert _count(db, cid) == 2
    _, a = _action(client, co, cid)
    assert a["label"] == AGAIN and a["last_teach_back"]["attempts"] == 2 and a["last_teach_back"]["score"] == 2


def test_passed_check_is_not_offered_or_accepted_again(client, as_user, new_case, db):
    cid, co = _notified(client, as_user, new_case)
    assert _tb(client, co, cid, WRONG).json()["passed"] is False
    r = _tb(client, co, cid, CORRECT)
    assert r.status_code == 200 and r.json()["passed"] is True
    assert r.json()["case"]["state"] == "acknowledged"
    d, a = _action(client, co, cid)
    assert a is None and d["state"] == "acknowledged"
    n = _count(db, cid)
    r = _tb(client, co, cid, CORRECT)
    assert r.status_code == 409 and "already recorded" in r.text and "understood" in r.text
    r = _tb(client, co, cid, WRONG)
    assert r.status_code == 409
    assert _count(db, cid) == n
    d, a = _action(client, co, cid)
    assert a is None and d["state"] == "acknowledged"
