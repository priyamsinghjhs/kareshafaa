"""Patient app appointment REQUESTS (screening, next screening from a due recall, follow-up time). Patients never
book: each request reaches the coordinator work list with an answer-by time and is confirmed (booked) or declined
with a reason; the patient is told in their language (simulated message). Server-side: a patient only ever
requests for themselves; staff RBAC unchanged."""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from kareshafaa.comms.request_text import REQ_MSG, REQ_TEXT
from kareshafaa.comms.service import SIM_PREFIX
from kareshafaa.models import (Appointment, AuditEvent, Case, Communication, Lesson, Organization, Patient,
                               PatientRequest, Recall, User, utcnow)
from kareshafaa.services import patient_requests as pr
from kareshafaa.statemachine import S

_n = itertools.count()
COORD = "aisha.rahman"


def _user_for(db, patient_id: int, prefix: str = "req.patient") -> User:
    u = db.scalars(select(User).where(User.patient_id == patient_id)).first()
    if u is None:
        u = User(username=f"{prefix}{next(_n)}.{patient_id}", display_name="Request Patient", role="patient",
                 patient_id=patient_id)
        db.add(u)
        db.commit()
    return u


def _clear(db, patient_id: int) -> None:
    for r in db.scalars(select(PatientRequest).where(PatientRequest.patient_id == patient_id,
                                                     PatientRequest.status == "open")):
        r.status = "withdrawn"  # left over from an earlier test on the same patient
    db.commit()


@pytest.fixture()
def follow_up(db, client, as_user, new_case):
    """A patient (own app login) whose case has an accepted referral and no appointment yet (built through the
    staff API: GP review, referral, specialist accepts)."""
    from tests.test_journey_api import SUMMARY, _post, notify_and_ack

    cid = new_case("colorectal_fit", 250)["id"]
    gp, sp = as_user("omar.haddad"), as_user("vikram.mehta")
    notify_and_ack(client, as_user, cid)
    assert _post(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed").status_code == 200
    assert _post(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, summary=SUMMARY).status_code == 200
    assert _post(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="accepted").status_code == 200
    db.expire_all()
    c = db.get(Case, cid)
    assert c.state == S.REFERRAL_ACCEPTED.value
    if c.target_due_at is None or c.target_due_at < utcnow() + timedelta(days=3):
        c.target_due_at = utcnow() + timedelta(days=10)
        db.commit()
    u = _user_for(db, c.patient_id)
    return {"case": cid, "patient_id": c.patient_id, "h": as_user(u.username), "user": u.username}


@pytest.fixture()
def screener(db, as_user, make_patient):
    pid = make_patient(language="ar")
    u = _user_for(db, pid)
    return {"patient_id": pid, "h": as_user(u.username)}


def _site(db) -> int:
    return db.scalar(select(Organization.id).where(Organization.kind == "primary_care", Organization.active.is_(True)))


def _screen(client, h, db, **kw):
    body = {"programme": "cervical", "site_org_id": _site(db), "days": ["mon", "wed"], "times": ["morning"], **kw}
    return client.post("/api/me/requests/screening", headers=h, json=body)


# ------------------------------------------------------------------ texts
def test_texts_have_the_same_keys_in_every_language():
    def keys(d, p=""):
        out = set()
        for k, v in d.items():
            out |= keys(v, f"{p}{k}.") if isinstance(v, dict) else {p + k}
        return out
    base = keys(REQ_TEXT["en"])
    for lang in ("ar", "hi", "ur"):
        assert keys(REQ_TEXT[lang]) == base, lang
        assert set(REQ_MSG[lang]) == set(REQ_MSG["en"]) == {"received", "confirmed", "declined"}
    assert set(REQ_TEXT["en"]["decline_reason"]) == set(pr.DECLINE_REASONS)
    blob = repr(REQ_TEXT) + repr(REQ_MSG)
    assert "demo" not in blob.lower()


def test_profile_offers_request_action_and_options(client, screener):
    d = client.get("/api/me/profile?lang=ar", headers=screener["h"]).json()
    assert d["text"]["req"]["request_btn"] == REQ_TEXT["ar"]["request_btn"]
    assert d["requests"] == []
    o = d["request_options"]
    assert set(o["programmes"]) == {"colorectal_fit", "cervical", "breast", "diabetes_hba1c"}
    assert o["days"] == list(pr.DAYS) and o["sites"] and all(s["kind"] in pr.SITE_KINDS for s in o["sites"])
    assert set(o["programme_names"]) == set(o["programmes"])


# ------------------------------------------------------------------ screening
def test_screening_request_validates_and_is_never_a_booking(client, db, screener):
    h = screener["h"]
    assert _screen(client, h, db, programme="nope").status_code == 422
    assert _screen(client, h, db, days=[]).status_code == 422
    assert _screen(client, h, db, times=["midnight"]).status_code == 422
    lab = db.scalar(select(Organization.id).where(Organization.kind == "lab"))
    assert _screen(client, h, db, site_org_id=lab).status_code == 422
    r = _screen(client, h, db, note="Mornings before work")
    assert r.status_code == 200, r.text
    assert r.json()["booked"] is False
    assert _screen(client, h, db).status_code == 409  # one open request per programme
    db.expire_all()
    req = db.get(PatientRequest, r.json()["request_id"])
    assert (req.kind, req.status, req.preferred_days, req.preferred_times, req.language) == ("screening", "open", ["mon", "wed"], ["morning"], "ar")
    assert req.due_at and req.due_at > req.created_at
    assert not db.scalar(select(Appointment.id).join(Case, Case.id == Appointment.case_id).where(Case.patient_id == screener["patient_id"]))
    m = db.scalar(select(Communication).where(Communication.patient_id == screener["patient_id"],
                                              Communication.category == "appointment_request"))
    assert m.simulated and m.content.startswith(SIM_PREFIX) and m.language == "ar"
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "patient_request.created", AuditEvent.entity_id == str(req.id)))
    mine = client.get("/api/me/profile", headers=h).json()["requests"]
    assert mine[0]["id"] == req.id and mine[0]["status"] == "open"


def test_patient_can_withdraw_own_request_only(client, db, screener, as_user, make_patient):
    rid = _screen(client, screener["h"], db, programme="breast").json()["request_id"]
    other = _user_for(db, make_patient())
    assert client.post(f"/api/me/requests/{rid}/withdraw", headers=as_user(other.username), json={}).status_code == 404
    assert client.post(f"/api/me/requests/{rid}/withdraw", headers=screener["h"], json={}).status_code == 200
    assert client.post(f"/api/me/requests/{rid}/withdraw", headers=screener["h"], json={}).status_code == 409
    db.expire_all()
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "patient_request.withdrawn", AuditEvent.entity_id == str(rid)))


def test_recall_request_is_own_only_and_marks_the_recall_invited(client, db, screener, as_user, make_patient):
    other = make_patient()
    due = (utcnow() + timedelta(days=20)).date()
    mine = Recall(patient_id=screener["patient_id"], programme="colorectal_fit", due_on=due, status="due", reason="Routine recall", interval_months=24, created_by="test")
    theirs = Recall(patient_id=other, programme="colorectal_fit", due_on=due, status="due", reason="Routine recall", interval_months=24, created_by="test")
    db.add_all([mine, theirs])
    db.commit()
    loop = client.get("/api/me/profile", headers=screener["h"]).json()["loop"]
    assert any((x.get("book_recall") or {}).get("recall_id") == mine.id for x in loop)
    assert _screen(client, screener["h"], db, programme="colorectal_fit", recall_id=theirs.id).status_code == 404
    assert _screen(client, screener["h"], db, programme="cervical", recall_id=mine.id).status_code == 422
    r = _screen(client, screener["h"], db, programme="colorectal_fit", recall_id=mine.id)
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    loop = client.get("/api/me/profile", headers=screener["h"]).json()["loop"]
    assert not any((x.get("book_recall") or {}).get("recall_id") == mine.id for x in loop)  # no second button
    # coordinator confirms a time and site
    co = as_user(COORD)
    start = (utcnow() + timedelta(days=9)).replace(microsecond=0)
    assert client.post(f"/api/patient-requests/{rid}/confirm", headers=co, json={}).status_code == 422  # time needed
    ok = client.post(f"/api/patient-requests/{rid}/confirm", headers=co, json={"start": start.isoformat() + "Z", "org_id": _site(db)})
    assert ok.status_code == 200, ok.text
    db.expire_all()
    req = db.get(PatientRequest, rid)
    assert (req.kind, req.status, req.confirmed_org_id) == ("recall", "confirmed", _site(db))
    assert db.get(Recall, mine.id).status == "invited" and db.get(Recall, theirs.id).status == "due"
    msg = db.get(Communication, req.notified_comm_id)
    assert msg.template_key == "appointment_request.confirmed" and msg.simulated and msg.language == "ar"
    assert client.post(f"/api/patient-requests/{rid}/decline", headers=co,
                       json={"reason_code": "other", "note": "too late now"}).status_code == 409  # already answered


# ------------------------------------------------------------------ follow-up
def test_follow_up_slots_stay_inside_the_case_deadline(client, db, follow_up):
    d = client.get("/api/me/profile", headers=follow_up["h"]).json()
    lp = next(x for x in d["loop"] if x.get("case_id") == follow_up["case"])
    ct = lp["choose_time"]
    assert ct and ct["tier_label"] in {"T0", "T1", "T2", "T3"}
    c = db.get(Case, follow_up["case"])
    assert ct["slots"] and len(ct["slots"]) <= pr.MAX_SLOTS
    for s in ct["slots"]:
        t = datetime.fromisoformat(s.rstrip("Z"))
        assert utcnow() < t <= c.target_due_at


def test_follow_up_pick_is_booked_only_by_a_coordinator(client, db, follow_up, as_user):
    h = follow_up["h"]
    slots = client.get("/api/me/profile", headers=h).json()["loop"]
    slot = next(x for x in slots if x.get("case_id") == follow_up["case"])["choose_time"]["slots"][0]
    u = "/api/me/requests/follow-up"
    assert client.post(u, headers=h, json={"case_id": follow_up["case"]}).status_code == 422  # neither slot nor barrier
    bad = (utcnow() + timedelta(days=60)).replace(microsecond=0).isoformat() + "Z"
    assert client.post(u, headers=h, json={"case_id": follow_up["case"], "slot": bad}).status_code == 422  # not offered
    r = client.post(u, headers=h, json={"case_id": follow_up["case"], "slot": slot})
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    assert client.post(u, headers=h, json={"case_id": follow_up["case"], "slot": slot}).status_code == 409
    db.expire_all()
    assert db.get(Case, follow_up["case"]).state != S.APPOINTMENT_BOOKED.value  # nothing booked yet
    # work list: only coordinators decide
    for who in ("omar.haddad", "fatima.al.mansoori", "admin", "vikram.mehta"):
        hh = as_user(who)
        assert client.post(f"/api/patient-requests/{rid}/confirm", headers=hh, json={}).status_code == 403, who
    co = as_user(COORD)
    wl = client.get("/api/patient-requests", headers=co).json()
    row = next(x for x in wl["items"] if x["id"] == rid)
    assert row["rag"] in {"green", "amber", "red"} and row["due_at"] and row["tier_label"].startswith("T")
    ok = client.post(f"/api/patient-requests/{rid}/confirm", headers=co, json={})
    assert ok.status_code == 200, ok.text
    db.expire_all()
    req = db.get(PatientRequest, rid)
    a = db.get(Appointment, req.appointment_id)
    assert a.case_id == follow_up["case"] and a.status == "booked" and a.start == req.chosen_start
    assert db.get(Case, follow_up["case"]).state == S.APPOINTMENT_BOOKED.value
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "patient_request.confirmed", AuditEvent.entity_id == str(rid)))


def test_follow_up_change_records_barrier_and_decline_needs_reason(client, db, follow_up, as_user):
    h = follow_up["h"]
    u = "/api/me/requests/follow-up"
    assert client.post(u, headers=h, json={"case_id": follow_up["case"], "barrier_code": "nope"}).status_code == 422
    r = client.post(u, headers=h, json={"case_id": follow_up["case"], "barrier_code": "childcare",
                                        "preferred_times": "After 3pm", "note": "School run"})
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    db.expire_all()
    req = db.get(PatientRequest, rid)
    assert (req.barrier_code, req.preferred_text, req.chosen_start) == ("childcare", "After 3pm", None)
    assert db.scalar(select(Lesson.id).where(Lesson.case_id == follow_up["case"], Lesson.kind == "appointment_change_request",
                                             Lesson.reason_code == "childcare")) is not None
    co = as_user(COORD)
    d = f"/api/patient-requests/{rid}/decline"
    assert client.post(d, headers=co, json={"reason_code": "no_capacity", "note": ""}).status_code == 422
    assert client.post(d, headers=co, json={"reason_code": "made_up", "note": "We will call you"}).status_code == 422
    assert client.post(d, headers=as_user("fatima.al.mansoori"), json={"reason_code": "no_capacity", "note": "We will call"}).status_code == 403
    ok = client.post(d, headers=co, json={"reason_code": "no_capacity", "note": "We will call you with a time"})
    assert ok.status_code == 200, ok.text
    db.expire_all()
    req = db.get(PatientRequest, rid)
    assert (req.status, req.decline_reason) == ("declined", "no_capacity")
    m = db.get(Communication, req.notified_comm_id)
    p = db.get(Patient, follow_up["patient_id"])
    lang = req.language
    assert m.template_key == "appointment_request.declined" and m.simulated and m.language == lang
    assert REQ_TEXT[lang]["decline_reason"]["no_capacity"] in m.content
    assert p is not None
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "patient_request.declined", AuditEvent.entity_id == str(rid)))
    mine = client.get("/api/me/profile", headers=h).json()["requests"]
    assert next(x for x in mine if x["id"] == rid)["decline_reason"] == "no_capacity"


@pytest.mark.parametrize("lang", ["en", "ar", "hi", "ur"])
def test_decline_message_in_each_language(client, db, as_user, make_patient, lang):
    pid = make_patient(language=lang)
    h = as_user(_user_for(db, pid).username)
    rid = _screen(client, h, db).json()["request_id"]
    ok = client.post(f"/api/patient-requests/{rid}/decline", headers=as_user(COORD),
                     json={"reason_code": "not_due", "note": "Your screening is not due yet"})
    assert ok.status_code == 200, ok.text
    db.expire_all()
    msgs = db.scalars(select(Communication).where(Communication.patient_id == pid, Communication.category == "appointment_request")
                      .order_by(Communication.id)).all()
    assert [m.template_key for m in msgs] == ["appointment_request.received", "appointment_request.declined"]
    assert all(m.language == lang and m.simulated for m in msgs)
    assert REQ_TEXT[lang]["decline_reason"]["not_due"] in msgs[-1].content


# ------------------------------------------------------------------ own-only + RBAC
def test_patient_cannot_request_for_someone_elses_case(client, db, follow_up, as_user, make_patient):
    other = _user_for(db, make_patient())
    r = client.post("/api/me/requests/follow-up", headers=as_user(other.username),
                    json={"case_id": follow_up["case"], "barrier_code": "work"})
    assert r.status_code == 404


def test_staff_cannot_use_patient_endpoints_and_patients_cannot_decide(client, db, screener, as_user):
    for who in (COORD, "omar.haddad", "fatima.al.mansoori", "admin"):
        assert _screen(client, as_user(who), db).status_code == 403, who
    rid = _screen(client, screener["h"], db, programme="diabetes_hba1c").json()["request_id"]
    assert client.get("/api/patient-requests", headers=screener["h"]).status_code == 403
    assert client.post(f"/api/patient-requests/{rid}/decline", headers=screener["h"],
                       json={"reason_code": "other", "note": "self-declined"}).status_code == 403
    for who in ("omar.haddad", "fatima.al.mansoori", "admin", "analyst"):
        assert client.get("/api/patient-requests", headers=as_user(who)).status_code == 403, who
