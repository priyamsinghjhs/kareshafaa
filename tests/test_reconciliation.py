"""Result-to-case reconciliation (principle 6): a source result that needs action but never became a case is found,
listed for its owner (care coordination, Results inbox) and put back into the loop by a person."""
from sqlalchemy import select

from kareshafaa.models import Case, DiagnosticReport, IngestMessage, ResultReconciliationItem as Item, utcnow
from kareshafaa.services import reconcile

import pytest

CO = "aisha.rahman"
_MADE: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup(db):
    """Orphan results are made on purpose here; remove any still without a case so the global safety scans
    (tests/safety/invariants.py: no actionable result without a case) stay meaningful for the rest of the run."""
    yield
    db.rollback()
    for rid in _MADE:
        rep = db.get(DiagnosticReport, rid)
        if rep is not None and rep.case_id is None:
            for it in db.scalars(select(Item).where(Item.report_id == rid)):
                db.delete(it)
            db.flush()
            db.delete(rep)
    db.commit()
    _MADE.clear()


def _orphan(db, make_patient, conclusion="actionable", programme="colorectal_fit"):
    pid = make_patient()
    rep = DiagnosticReport(patient_id=pid, programme=programme, category="screening", code="29771-3", display="FIT",
                           conclusion=conclusion, issued_at=utcnow(), performer_org_id=1, status="final")
    db.add(rep)
    db.commit()
    _MADE.append(rep.id)
    return rep.id, pid


def test_result_without_case_is_flagged_and_listed(client, as_user, db, make_patient):
    rid, _pid = _orphan(db, make_patient)
    out = reconcile.run(db)
    db.commit()
    assert out["new"] >= 1
    it = db.scalar(select(Item).where(Item.report_id == rid, Item.status == "open"))
    assert it is not None and it.kind == "result_without_case" and it.owner_role == "coordinator"
    # idempotent: a second run creates no duplicate
    reconcile.run(db)
    db.commit()
    assert db.scalar(select(Item).where(Item.report_id == rid).with_only_columns(Item.id).limit(2)) == it.id
    assert len(db.scalars(select(Item).where(Item.report_id == rid)).all()) == 1
    r = client.get("/api/ingest/reconciliation", headers=as_user(CO))
    assert r.status_code == 200 and rid in {i["report_id"] for i in r.json()["items"]}


def test_result_with_case_is_not_flagged(db, new_case):
    c = new_case()
    reconcile.run(db)
    db.commit()
    rep_id = db.get(Case, c["id"]).report_id
    assert db.scalar(select(Item).where(Item.report_id == rep_id)) is None


def test_negative_result_is_not_flagged(db, make_patient):
    rid, _ = _orphan(db, make_patient, conclusion="negative")
    reconcile.run(db)
    db.commit()
    assert db.scalar(select(Item).where(Item.report_id == rid)) is None


def test_owner_opens_a_review_case_from_the_item(client, as_user, db, make_patient):
    rid, pid = _orphan(db, make_patient)
    reconcile.run(db)
    db.commit()
    it = db.scalar(select(Item).where(Item.report_id == rid, Item.status == "open"))
    bad = client.post(f"/api/ingest/reconciliation/{it.id}/resolve", headers=as_user(CO), json={"action": "note", "note": "looks fine to me"})
    assert bad.status_code in (400, 422)  # a result that needs action is never dismissed with a note
    r = client.post(f"/api/ingest/reconciliation/{it.id}/resolve", headers=as_user(CO), json={"action": "open_case"})
    assert r.status_code == 200, r.text
    db.expire_all()
    case = db.get(Case, r.json()["item"]["resolution_case_id"])
    assert case.patient_id == pid and case.review_required and "missing_from_loop" in case.review_reasons
    assert case.tier in ("critical", "urgent")
    assert db.get(DiagnosticReport, rid).case_id == case.id
    assert db.get(Item, it.id).status == "resolved"


def test_linked_later_is_closed_automatically(db, make_patient, new_case):
    rid, _ = _orphan(db, make_patient)
    reconcile.run(db)
    db.commit()
    c = new_case()
    db.get(DiagnosticReport, rid).case_id = c["id"]
    db.commit()
    out = reconcile.run(db)
    db.commit()
    it = db.scalar(select(Item).where(Item.report_id == rid))
    assert it.status == "resolved" and it.resolution == "linked_later" and out["linked_later"] >= 1


def test_processed_message_without_result_is_flagged_and_noted(client, as_user, db):
    m = IngestMessage(source="hl7v2", message_id=f"REC-{utcnow().timestamp()}", status="processed", programme="breast")
    db.add(m)
    db.commit()
    reconcile.run(db)
    db.commit()
    it = db.scalar(select(Item).where(Item.ingest_message_id == m.id, Item.status == "open"))
    assert it.kind == "message_without_result"
    r = client.post(f"/api/ingest/reconciliation/{it.id}/resolve", headers=as_user(CO),
                    json={"action": "note", "note": "Test message from the lab interface, no patient result"})
    assert r.status_code == 200, r.text


def test_list_and_actions_are_coordination_only(client, as_user):
    for u in ("admin", "analyst", "patient.en", "vikram.mehta"):
        assert client.get("/api/ingest/reconciliation", headers=as_user(u)).status_code == 403


def test_admin_system_shows_monitoring_counts(client, as_user, db, make_patient):
    _orphan(db, make_patient)
    r = client.post("/api/admin/system/result-reconciliation/run", headers=as_user("admin"))
    assert r.status_code == 200 and r.json()["open"] >= 1
    s = client.get("/api/admin/system", headers=as_user("admin")).json()["monitoring"]
    assert s["result_reconciliation"]["open"] >= 1
    assert {j["name"] for j in s["jobs"]} >= {"sla_sweep", "audit_reconcile", "result_reconcile"}
    assert next(j for j in s["jobs"] if j["name"] == "result_reconcile")["ok"] is True
    assert "audit_fallback" in client.get("/api/admin/system", headers=as_user("admin")).json()
    assert client.get("/api/admin/system", headers=as_user("fatima.al.mansoori")).status_code == 403
    assert client.post("/api/admin/system/result-reconciliation/run", headers=as_user("fatima.al.mansoori")).status_code == 403


def test_job_health_records_success_and_failure(db):
    import pytest
    from kareshafaa.models import BackgroundJobStatus
    from kareshafaa.services import job_health
    job_health.track("ml_scoring", lambda: {"n": 3})
    with pytest.raises(ZeroDivisionError):
        job_health.track("ml_scoring", lambda: 1 / 0)
    db.expire_all()
    row = db.get(BackgroundJobStatus, "ml_scoring")
    assert row.failures >= 1 and row.last_error == "ZeroDivisionError" and row.last_summary == {"n": 3}
    st = next(j for j in job_health.status(db) if j["name"] == "ml_scoring")
    assert st["ok"] is False
