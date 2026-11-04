"""Patient answer lock: once a patient has sent their teach-back answers from /patient, a repeat submission is
refused server-side unless that same patient explicitly asked to change them ("Change my answers"). History is
kept (one response row per submission) and the reopen + the change are in the hash-chained audit log."""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kareshafaa import accounts, audit
from kareshafaa.models import AnswerReopen, AuditEvent, QuestionnaireResponse

PW = "amber-lagoon-falcon-58"
WRONG = {"q1": "b", "q2": "b", "q3": "a"}
RIGHT = {"q1": "a", "q2": "b", "q3": "a"}
_n = itertools.count()


def _tb(answers, conf=3, lang="en"):
    return {"answers": answers, "confidence": conf, "language": lang}


@pytest.fixture()
def patient_case(client, as_user, new_case, db):
    """A fresh patient whose case is at 'patient_notified', with their own patient login. Returns
    (case_id, bearer headers, username, patient_id)."""
    from kareshafaa.models import Case, User

    def _mk(language: str = "en"):
        c = new_case("colorectal_fit", 250, language=language)
        co = as_user("aisha.rahman")
        assert client.post(f"/api/cases/{c['id']}/notify", headers=co, json={"category": "result_notification"}).status_code == 200
        r = client.post(f"/api/cases/{c['id']}/call", headers=co, json={"outcome": "reached", "note": "explained result"})
        assert r.json()["case"]["state"] == "patient_notified", r.text
        db.expire_all()
        pid = db.get(Case, c["id"]).patient_id
        u = User(username=f"lock.patient{next(_n)}.{pid}", display_name="Test Patient", role="patient", patient_id=pid)
        accounts.set_password(u, PW)
        db.add(u)
        db.flush()
        tok = accounts.create_session(db, u)
        db.commit()
        return c["id"], {"Authorization": f"Bearer {tok}"}, u.username, pid

    return _mk


def _events(db, cid, name):
    db.expire_all()
    return db.scalars(select(AuditEvent).where(AuditEvent.case_id == cid, AuditEvent.event == name)
                      .order_by(AuditEvent.id)).all()


def test_first_submit_works_and_page_shows_saved_answers(client, patient_case):
    cid, h, _, _ = patient_case()
    before = client.get("/api/me/case", headers=h).json()
    assert before["can_teach_back"] and before["answers_lock"]["submitted"] is None
    assert not before["answers_lock"]["locked"]
    r = client.post("/api/me/teach-back", headers=h, json=_tb(WRONG, 2))
    assert r.status_code == 200 and r.json()["passed"] is False and r.json()["changed"] is False
    after = client.get("/api/me/case", headers=h).json()
    lk = after["answers_lock"]
    assert after["can_teach_back"] is False  # options are no longer selectable
    assert lk["locked"] and lk["can_change"] and not lk["reopened"] and not lk["changed"]
    assert lk["submitted"]["answers"] == WRONG and lk["submitted"]["confidence"] == 2
    assert lk["submitted"]["submitted_at"]


def test_second_submit_is_blocked_without_reopen(client, patient_case, db):
    cid, h, _, _ = patient_case()
    assert client.post("/api/me/teach-back", headers=h, json=_tb(WRONG)).status_code == 200
    r = client.post("/api/me/teach-back", headers=h, json=_tb(RIGHT, 5))
    assert r.status_code == 409 and r.json()["code"] == "answers_locked"
    db.expire_all()
    rows = db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id == cid)).all()
    assert len(rows) == 1 and rows[0].answers == WRONG  # nothing was saved or overwritten
    from kareshafaa.models import Case
    assert db.get(Case, cid).state == "patient_notified"  # no clinical side effect from the refused post


def test_reopen_then_one_resubmit_keeps_history_and_audits(client, patient_case, db):
    cid, h, _, _ = patient_case()
    assert client.post("/api/me/teach-back", headers=h, json=_tb(WRONG, 2)).status_code == 200
    assert client.post("/api/me/teach-back/reopen", headers=h, json={}).status_code == 422  # must confirm
    assert client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": False}).status_code == 422
    r = client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True})
    assert r.status_code == 200 and r.json()["expires_at"]
    page = client.get("/api/me/case", headers=h).json()
    assert page["can_teach_back"] and page["answers_lock"]["reopened"] and not page["answers_lock"]["locked"]
    r = client.post("/api/me/teach-back", headers=h, json=_tb(RIGHT, 4))
    assert r.status_code == 200 and r.json()["passed"] is True and r.json()["changed"] is True
    assert r.json()["state"] == "acknowledged"  # clinical rule unchanged: a pass acknowledges
    # the reopen is consumed: a third post is refused again
    assert client.post("/api/me/teach-back", headers=h, json=_tb(WRONG)).status_code == 409
    db.expire_all()
    rows = db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id == cid)
                      .order_by(QuestionnaireResponse.id)).all()
    assert [q.answers for q in rows] == [WRONG, RIGHT]  # previous answers kept in history
    lock = client.get("/api/me/case", headers=h).json()["answers_lock"]
    assert lock["locked"] and lock["changed"] and lock["submitted"]["answers"] == RIGHT
    req = _events(db, cid, "teachback.reopen_requested")
    chg = _events(db, cid, "teachback.answers_changed")
    assert len(req) == 1 and req[0].detail["previous"]["answers"] == WRONG and req[0].actor_role == "patient"
    assert len(chg) == 1 and chg[0].detail["previous"]["answers"] == WRONG and chg[0].detail["new"]["answers"] == RIGHT
    assert len(_events(db, cid, "teachback.submitted")) == 2
    assert audit.verify_chain(db)["valid"]


def test_staff_view_shows_patient_changed_answers(client, as_user, patient_case):
    cid, h, _, _ = patient_case()
    client.post("/api/me/teach-back", headers=h, json=_tb(WRONG))
    client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True})
    client.post("/api/me/teach-back", headers=h, json=_tb(RIGHT))
    tb = client.get(f"/api/cases/{cid}", headers=as_user("aisha.rahman")).json()["teach_back"]
    assert [t["patient_changed_answers"] for t in tb] == [False, True]
    assert tb[1]["replaces_id"] == tb[0]["id"] and tb[0]["answers"] == WRONG and tb[1]["answers"] == RIGHT


def test_cancel_reopen_locks_again(client, patient_case, db):
    cid, h, _, _ = patient_case()
    client.post("/api/me/teach-back", headers=h, json=_tb(WRONG))
    client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True})
    assert client.post("/api/me/teach-back/reopen/cancel", headers=h, json={}).json()["cancelled"] is True
    assert client.post("/api/me/teach-back", headers=h, json=_tb(RIGHT)).status_code == 409
    assert client.get("/api/me/case", headers=h).json()["answers_lock"]["locked"]
    assert len(_events(db, cid, "teachback.reopen_cancelled")) == 1


def test_expired_reopen_does_not_unlock(client, patient_case, db):
    cid, h, _, _ = patient_case()
    client.post("/api/me/teach-back", headers=h, json=_tb(WRONG))
    client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True})
    db.expire_all()
    ro = db.scalars(select(AnswerReopen).where(AnswerReopen.case_id == cid)).one()
    ro.expires_at = ro.requested_at - timedelta(seconds=1)
    db.commit()
    assert client.post("/api/me/teach-back", headers=h, json=_tb(RIGHT)).status_code == 409


def test_other_patients_unaffected(client, patient_case):
    cid_a, ha, _, _ = patient_case()
    cid_b, hb, _, _ = patient_case("ar")
    client.post("/api/me/teach-back", headers=ha, json=_tb(WRONG))
    client.post("/api/me/teach-back/reopen", headers=ha, json={"confirm": True})
    # B has not answered yet: first submission works, and A's reopen does not unlock B's repeat
    page_b = client.get("/api/me/case", headers=hb).json()
    assert page_b["can_teach_back"] and page_b["answers_lock"]["submitted"] is None
    assert client.post("/api/me/teach-back", headers=hb, json=_tb(RIGHT, 4, "ar")).status_code == 200
    assert client.post("/api/me/teach-back", headers=hb, json=_tb(WRONG, 4, "ar")).status_code == 409
    # A still has exactly one change available
    assert client.post("/api/me/teach-back", headers=ha, json=_tb(RIGHT)).status_code == 200
    assert client.post("/api/me/teach-back", headers=ha, json=_tb(RIGHT)).status_code == 409


def test_reopen_needs_saved_answers_and_patient_role(client, as_user, patient_case):
    cid, h, _, _ = patient_case()
    assert client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True}).status_code == 409
    assert client.post("/api/me/teach-back/reopen", headers=as_user("aisha.rahman"),
                       json={"confirm": True}).status_code == 403


def test_navigator_teach_back_still_works_and_does_not_lock_patient(client, as_user, patient_case):
    cid, h, _, _ = patient_case()
    co = as_user("aisha.rahman")
    r = client.post(f"/api/cases/{cid}/teach-back", headers=co, json=_tb(WRONG))
    assert r.status_code == 200, r.text
    assert client.post("/api/me/teach-back", headers=h, json=_tb(RIGHT)).status_code == 200  # patient's first own answer
    r = client.post(f"/api/cases/{cid}/teach-back", headers=co, json=_tb(RIGHT))
    assert r.status_code == 200  # staff can still record a navigator teach-back after the patient's answers


def test_cookie_session_needs_csrf_for_submit_and_reopen(app, patient_case):
    cid, _, username, _ = patient_case()
    c = TestClient(app)
    c.get("/login")
    r = c.post("/login", data={"username": username, "password": PW, "csrf_token": c.cookies.get("kareshafaa_csrf"),
                               "next": ""}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/patient").status_code == 200
    tok = c.get("/api/auth/me").json()["csrf"]
    assert c.post("/api/me/teach-back", json=_tb(WRONG)).status_code == 403  # no CSRF header
    assert c.post("/api/me/teach-back", json=_tb(WRONG), headers={"X-CSRF-Token": tok}).status_code == 200
    assert c.post("/api/me/teach-back/reopen", json={"confirm": True}).status_code == 403
    assert c.post("/api/me/teach-back/reopen", json={"confirm": True}, headers={"X-CSRF-Token": tok}).status_code == 200
    assert c.post("/api/me/teach-back", json=_tb(RIGHT), headers={"X-CSRF-Token": tok}).status_code == 200


def test_lock_text_translated_for_every_patient_language():
    from kareshafaa.comms.content import ANSWER_LOCK_TEXT, SUPPORTED_LANGUAGES, UI_TEXT
    keys = set(ANSWER_LOCK_TEXT["en"])
    for lang in SUPPORTED_LANGUAGES:
        assert set(ANSWER_LOCK_TEXT[lang]) == keys and all(ANSWER_LOCK_TEXT[lang].values()), lang
        assert "{when}" in UI_TEXT[lang]["saved_on"], lang
        assert "demo" not in " ".join(ANSWER_LOCK_TEXT[lang].values()).lower()
    assert ANSWER_LOCK_TEXT["ar"]["change"] != ANSWER_LOCK_TEXT["en"]["change"]


def test_patient_page_has_lock_ui():
    from pathlib import Path
    import kareshafaa
    root = Path(kareshafaa.__file__).parent
    html = (root / "templates" / "patient.html").read_text()
    assert "js/patient_app.js" in html  # page script is a static file (CSP script-src 'self')
    html += (root / "static" / "js" / "patient_app.js").read_text()
    assert "/api/me/teach-back/reopen" in html and "answers_locked" in html and "q-saved" in html


def test_one_reopen_cannot_be_used_twice_in_parallel(client, patient_case, db):
    """Two posts that both passed the pre-check with the same reopen: only the first is accepted."""
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.comms import service as comms
    from kareshafaa.models import Case, User
    from kareshafaa.services import patient_answers as pa

    cid, h, username, _ = patient_case()
    client.post("/api/me/teach-back", headers=h, json=_tb(WRONG))
    client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True})
    db.expire_all()
    actor = actor_from_user(db.scalars(select(User).where(User.username == username)).one())
    case = db.get(Case, cid)
    ro1, ro2 = pa.guard_submit(db, case, actor), pa.guard_submit(db, case, actor)
    assert ro1 is not None and ro1.id == ro2.id
    qr = comms.submit_teach_back(db, case, actor, RIGHT, confidence=4, language="en")
    pa.finalize_submit(db, case, actor, qr, ro1)
    db.commit()
    qr2 = comms.submit_teach_back(db, case, actor, WRONG, confidence=4, language="en")
    with pytest.raises(pa.AnswersLocked):
        pa.finalize_submit(db, case, actor, qr2, ro2)
    db.rollback()
