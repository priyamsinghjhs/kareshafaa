"""Recall after a negative result / closed case / surveillance plan: list, invite, cancel, completion, KPI, RBAC."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select


def _neg(client, as_user, pid, programme="colorectal_fit"):
    r = client.post("/api/results", headers=as_user("aisha.rahman"),
                    json={"patient_id": pid, "programme": programme, "value": 3, "unit": "ug/g"})
    assert r.status_code == 200, r.text
    return r.json()


def _recall(db, pid):
    from kareshafaa.models import Recall
    db.expire_all()
    return db.scalars(select(Recall).where(Recall.patient_id == pid).order_by(Recall.id.desc())).first()


def test_negative_result_schedules_routine_recall_with_confirm_interval(client, as_user, db, make_patient):
    from kareshafaa.clinical_rules import RECALL_INTERVAL_MONTHS
    from kareshafaa.services.recall import add_months
    pid = make_patient(phone_valid=True)
    body = _neg(client, as_user, pid)
    assert not body["actionable"] and body.get("case") is None
    r = _recall(db, pid)
    assert r.status == "due" and r.reason == "routine_after_negative" and r.programme == "colorectal_fit"
    assert r.interval_months == RECALL_INTERVAL_MONTHS["colorectal_fit"] == 24
    assert abs((r.due_on - add_months(date.today(), 24)).days) <= 1
    # a second negative supersedes the first: one open recall per person and programme
    _neg(client, as_user, pid)
    from kareshafaa.models import Recall
    db.expire_all()
    rows = db.scalars(select(Recall).where(Recall.patient_id == pid)).all()
    assert sorted(x.status for x in rows) == ["completed", "due"]  # the new result completed the earlier recall


def test_recall_list_invite_and_completion_by_new_result(client, as_user, db, make_patient):
    from kareshafaa.models import AuditEvent, Communication
    pid = make_patient(phone_valid=True, language="ar")
    _neg(client, as_user, pid)
    r = _recall(db, pid)
    r.due_on = date.today() - timedelta(days=3)  # make it due now
    db.commit()
    h = as_user("aisha.rahman")
    lst = client.get("/api/recalls?view=overdue", headers=h).json()
    row = next(x for x in lst["items"] if x["id"] == r.id)
    assert row["days_overdue"] == 3 and lst["can_manage"] is True
    inv = client.post(f"/api/recalls/{r.id}/invite", headers=h)
    assert inv.status_code == 200, inv.text
    com = db.scalars(select(Communication).where(Communication.patient_id == pid, Communication.category == "recall_invite")).one()
    assert com.content.startswith("[SIMULATED - not sent] ") and com.language == "ar" and "مرحباً" in com.content
    assert _recall(db, pid).status == "invited" and _recall(db, pid).invite_count == 1
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "recall.invited", AuditEvent.entity_id == str(r.id)))
    s = client.get("/api/recalls/summary", headers=h).json()
    assert s["invited_open"] >= 1
    # the recall comes back as an actionable result -> new case (new episode) linked to the recall
    c = client.post("/api/results", headers=h, json={"patient_id": pid, "programme": "colorectal_fit", "value": 260, "unit": "ug/g"}).json()
    done = db.get(type(r), r.id)
    db.refresh(done)
    assert done.status == "completed" and done.completed_case_id == c["case"]["id"] and done.completed_report_id


def test_cancel_needs_reason_and_is_audited(client, as_user, db, make_patient):
    pid = make_patient(phone_valid=True)
    _neg(client, as_user, pid)
    r = _recall(db, pid)
    h = as_user("aisha.rahman")
    assert client.post(f"/api/recalls/{r.id}/cancel", headers=h, json={"reason": "no"}).status_code == 422
    ok = client.post(f"/api/recalls/{r.id}/cancel", headers=h, json={"reason": "Screened privately in Dubai – report seen"})
    assert ok.status_code == 200 and ok.json()["recall"]["status"] == "cancelled"
    assert _recall(db, pid).closed_note == "Screened privately in Dubai – report seen"
    assert client.post(f"/api/recalls/{r.id}/invite", headers=h).status_code == 422  # no longer open


def test_no_channel_and_bulk_invite_partial(client, as_user, db, make_patient):
    a, b = make_patient(phone_valid=True), make_patient(phone_valid=False)
    _neg(client, as_user, a); _neg(client, as_user, b)
    ra, rb = _recall(db, a), _recall(db, b)
    h = as_user("aisha.rahman")
    assert client.post(f"/api/recalls/{rb.id}/invite", headers=h).status_code == 409
    res = client.post("/api/recalls/invite", headers=h, json={"ids": [ra.id, rb.id]}).json()
    assert [x["id"] for x in res["sent"]] == [ra.id] and res["failed"][0]["id"] == rb.id


def test_rbac(client, as_user, db, make_patient):
    pid = make_patient(phone_valid=True)
    _neg(client, as_user, pid)
    r = _recall(db, pid)
    assert client.get("/api/recalls", headers=as_user("omar.haddad")).status_code == 200
    lead = as_user("fatima.al.mansoori")
    lst = client.get("/api/recalls?view=upcoming", headers=lead)
    assert lst.status_code == 200 and lst.json()["can_manage"] is False
    assert client.post(f"/api/recalls/{r.id}/invite", headers=lead).status_code == 403
    assert client.post(f"/api/recalls/{r.id}/cancel", headers=lead, json={"reason": "not allowed here"}).status_code == 403
    for u in ("vikram.mehta", "admin"):
        assert client.get("/api/recalls", headers=as_user(u)).status_code == 403, u
        assert client.post(f"/api/recalls/{r.id}/invite", headers=as_user(u)).status_code == 403, u


def test_after_closure_rules(db, new_case):
    from kareshafaa.models import CarePlan, Case, utcnow
    from kareshafaa.security import SYSTEM
    from kareshafaa.services import recall as rc
    now = utcnow()

    def case(**kw):
        c = db.get(Case, new_case(value=250)["id"])
        c.state, c.closed_at = "closed", now
        for k, v in kw.items():
            setattr(c, k, v)
        db.flush()
        return c
    c1 = case(closure_type="authorised_decision", closure_reason="moved_abroad")
    assert rc.after_closure(db, c1, SYSTEM, now) is None and c1.next_screening_due is None
    c2 = case(closure_type="authorised_decision", closure_reason="informed_decline")
    r2 = rc.after_closure(db, c2, SYSTEM, now)
    assert r2.reason == "after_closure" and c2.next_screening_due == rc.add_months(now.date(), 24)
    c3 = case(closure_type="evidence", closure_evidence_type="care_plan")
    cp = CarePlan(case_id=c3.id, kind="surveillance", description="Polyps removed; colonoscopy", created_at=now,
                  recorded_by_id=c3.owner_id, review_due_on=date(2029, 3, 1))
    db.add(cp); db.flush()
    c3.closure_evidence_id = cp.id
    r3 = rc.after_closure(db, c3, SYSTEM, now)
    assert r3.reason == "surveillance" and r3.due_on == date(2029, 3, 1) and r3.care_plan_id == cp.id
    c4 = case(closure_type="evidence", closure_evidence_type="care_plan")
    db.add(CarePlan(case_id=c4.id, kind="treatment", description="Surgery booked", created_at=now, recorded_by_id=c4.owner_id))
    db.flush()
    assert rc.after_closure(db, c4, SYSTEM, now) is None
    db.rollback()


def test_recall_kpi_on_dashboard(client, as_user):
    k = client.get("/api/analytics/kpis", headers=as_user("fatima.al.mansoori"))
    if k.status_code == 404:
        return
    assert "recall_uptake_rate" in k.json() and "recalls_due_now" in k.json()


def test_recall_page_and_nav(client, as_user):
    from kareshafaa.ui import nav_for
    assert any(x["href"] == "/recalls" for x in nav_for("coordinator"))
    assert not any(x["href"] == "/recalls" for x in nav_for("specialist"))
    assert client.get("/recalls", headers=as_user("aisha.rahman")).status_code == 200
