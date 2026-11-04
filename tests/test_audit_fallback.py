"""Request-audit failure after a committed change: the user still gets their answer, the event is retried once,
then kept durably (fallback table, else an append-only JSONL file) and later appended to the hash-chained trail
with its ORIGINAL timestamp, marked reconciled-late. Admin > System shows an alert with the count (admin only)."""
from __future__ import annotations

import concurrent.futures
import json

import pytest
from sqlalchemy import select

from kareshafaa import audit, audit_fallback, main
from kareshafaa.models import AuditEvent, AuditFallbackEvent
from kareshafaa.services.errors import ServiceBusy


def _failing_submit(fail_times: int, calls: list, marker: str):
    """Fail the first `fail_times` audit writes of requests whose path contains `marker` (others pass through)."""
    real = main._BATCHER.submit

    def submit(items):
        if not any(marker in (kw.get("path") or "") for _a, _x, _e, kw in items):
            return real(items)
        calls.append(items)
        if len(calls) <= fail_times:
            f = concurrent.futures.Future()
            f.set_exception(ServiceBusy())
            return f
        return real(items)
    return submit


@pytest.fixture()
def clean_fallback(db):
    """Start and end with nothing waiting (other tests share the database and data directory)."""
    audit_fallback.reconcile(db)
    yield
    audit_fallback.reconcile(db)
    p = audit_fallback._path()
    if p.exists():
        p.unlink()


def _edit_email(client, as_user, make_patient, n: int):
    pid = make_patient()
    return pid, client.post(f"/api/patients/{pid}/contact", headers=as_user("aisha.rahman"),
                            json={"email": f"fallback{n}@example.com"})


def test_retry_once_succeeds_without_fallback(client, as_user, make_patient, db, monkeypatch, clean_fallback):
    calls: list = []
    monkeypatch.setattr(main._BATCHER, "submit", _failing_submit(1, calls, "/contact"))
    before = audit_fallback.status(db)["unreconciled"]
    as_user("aisha.rahman")
    pid, r = _edit_email(client, as_user, make_patient, 1)
    assert r.status_code == 200, r.text
    assert len(calls) == 2  # first try + one retry
    db.expire_all()
    assert audit_fallback.status(db)["unreconciled"] == before
    ev = db.scalar(select(AuditEvent).where(AuditEvent.path == f"/api/patients/{pid}/contact",
                                            AuditEvent.event == "http.post"))
    assert ev is not None and not (ev.detail or {}).get("reconciled_late")


def test_failed_audit_goes_to_table_then_reconciles_with_original_time(client, as_user, make_patient, db,
                                                                         monkeypatch, clean_fallback):
    calls: list = []
    monkeypatch.setattr(main._BATCHER, "submit", _failing_submit(2, calls, "/contact"))
    pid, r = _edit_email(client, as_user, make_patient, 2)
    assert r.status_code == 200 and r.json()["ok"], r.text  # the committed change is still reported as done
    assert len(calls) == 2
    monkeypatch.undo()
    db.expire_all()
    path = f"/api/patients/{pid}/contact"
    assert db.scalar(select(AuditEvent).where(AuditEvent.path == path, AuditEvent.event == "http.post")) is None
    fb = db.scalar(select(AuditFallbackEvent).where(AuditFallbackEvent.path == path))
    assert fb is not None and fb.reconciled_at is None and fb.stored_in == "table" and "ServiceBusy" in fb.error
    assert fb.actor_role == "coordinator" and fb.event == "http.post"
    # admin System page: alert with the count; other roles cannot see it
    s = client.get("/api/admin/system", headers=as_user("admin")).json()["audit_fallback"]
    assert s["unreconciled"] >= 1 and s["oldest_at"]
    assert client.get("/api/admin/system", headers=as_user("aisha.rahman")).status_code == 403
    assert client.get("/api/admin/system", headers=as_user("fatima.al.mansoori")).status_code == 403
    assert client.post("/api/admin/system/audit-fallback/reconcile",
                       headers=as_user("fatima.al.mansoori")).status_code == 403
    # admin action reconciles into the hash chain
    out = client.post("/api/admin/system/audit-fallback/reconcile", headers=as_user("admin"))
    assert out.status_code == 200, out.text
    assert out.json()["reconciled"] >= 1 and out.json()["status"]["unreconciled"] == 0
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.path == path, AuditEvent.event == "http.post"))
    assert ev is not None and ev.ts == fb.ts  # original timestamp kept
    assert ev.detail["reconciled_late"] is True and ev.detail["fallback_id"] == fb.fallback_id
    assert ev.detail["status"] == 200
    fb2 = db.get(AuditFallbackEvent, fb.id)
    db.refresh(fb2)
    assert fb2.reconciled_at is not None and fb2.audit_event_id == ev.id
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "audit.fallback_reconciled")
                     .order_by(AuditEvent.id.desc())).actor_role == "admin"
    assert audit.verify_chain(db)["valid"]
    # running again appends nothing twice
    assert audit_fallback.reconcile(db)["reconciled"] == 0


def test_database_down_falls_back_to_jsonl_file(db, monkeypatch, clean_fallback):
    from kareshafaa.models import utcnow
    from kareshafaa.security import Actor

    def broken(*_a, **_k):
        raise RuntimeError("database unreachable")

    when = utcnow().replace(microsecond=0)
    items = [(Actor(user_id=7, role="gp", display="Dr Test"), "E", "http.post",
              dict(entity_type="http", entity_id=None, case_id=None, outcome="success", path="/api/x",
                   detail={"status": 200}, ts=when))]
    monkeypatch.setattr(audit_fallback, "insert", broken)
    assert audit_fallback.store(items, ServiceBusy()) == "file"
    monkeypatch.undo()
    p = audit_fallback._path()
    lines = p.read_text().splitlines()
    assert json.loads(lines[-1])["event"] == "http.post"
    with open(p, "a") as f:
        f.write('{"fallback_id": "torn')  # a crash mid-write leaves a torn last line: skipped, never fatal
    st = audit_fallback.status(db)
    assert st["in_file"] == 1 and st["unreconciled"] >= 1
    out = main._reconcile_audit_once()  # the timed-jobs worker's job
    assert out["imported_from_file"] == 1 and out["reconciled"] >= 1 and out["remaining"] == 0
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.path == "/api/x").order_by(AuditEvent.id.desc()))
    assert ev.ts == when and ev.detail["reconciled_late"] and ev.detail["stored_in"] == "file"
    assert ev.actor_role == "gp" and ev.actor_user_id == 7
    # the file is append-only: it is kept, and importing it again adds nothing
    assert p.exists() and audit_fallback.reconcile(db)["imported_from_file"] == 0
    assert audit.verify_chain(db)["valid"]


def test_last_resort_is_the_error_log(monkeypatch, caplog, clean_fallback):
    from kareshafaa.security import Actor

    def broken(*_a, **_k):
        raise RuntimeError("down")
    monkeypatch.setattr(audit_fallback, "insert", broken)
    monkeypatch.setattr(audit_fallback.os, "open", broken)
    n = audit_fallback.LOST_TO_LOG["count"]
    items = [(Actor(user_id=None, role="anonymous", display="anonymous"), "E", "http.post", dict(path="/api/y"))]
    assert audit_fallback.store(items, "boom") == "log"
    monkeypatch.undo()
    assert audit_fallback.LOST_TO_LOG["count"] == n + 1
    assert "AUDIT EVENT NOT STORED" in caplog.text and "/api/y" in caplog.text
    audit_fallback.LOST_TO_LOG["count"] = n


def test_read_served_when_its_audit_event_is_kept_in_the_fallback(client, as_user, monkeypatch, db,
                                                                   clean_fallback):
    calls: list = []
    monkeypatch.setattr(main._BATCHER, "submit", _failing_submit(5, calls, "/api/analytics/kpis"))
    r = client.get("/api/analytics/kpis", headers=as_user("analyst"))
    assert r.status_code == 200 and len(calls) == 1  # no retry on the read path: straight to the fallback
    monkeypatch.undo()
    st = audit_fallback.status(db)
    assert st["in_file"] == 1  # SQLite: the fsync'd file first, so no database lock is waited for
    out = audit_fallback.reconcile(db)
    assert out["reconciled"] == 1 and out["remaining"] == 0
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.path == "/api/analytics/kpis").order_by(AuditEvent.id.desc()))
    assert ev.detail["reconciled_late"] and ev.actor_role == "analyst" and ev.action == "R"


def test_read_refused_only_when_the_fallback_fails_too(client, as_user, monkeypatch, clean_fallback):
    calls: list = []
    monkeypatch.setattr(main._BATCHER, "submit", _failing_submit(5, calls, "/api/analytics/kpis"))
    monkeypatch.setattr(audit_fallback, "store", lambda *_a, **_k: "log")
    r = client.get("/api/analytics/kpis", headers=as_user("analyst"))
    assert r.status_code == 503 and r.headers.get("Retry-After")


def test_writer_lock_held_by_a_long_job_does_not_turn_a_read_into_503(client, as_user, monkeypatch, db,
                                                                      clean_fallback):
    """A long transaction (e.g. an SLA sweep over 20k open cases) holding the audit writer lock: the read's audit
    event waits at most request_audit_lock_timeout_s, then goes to the fallback; the read is served (200)."""
    import threading
    import time

    from kareshafaa.config import get_settings

    h = as_user("analyst")
    monkeypatch.setattr(get_settings(), "request_audit_lock_timeout_s", 0.3)
    held, done = threading.Event(), threading.Event()

    def long_job():
        audit._WRITER_LOCK.acquire(file_lock=False)
        held.set()
        done.wait(10)
        audit._WRITER_LOCK.release(file_lock=False)

    t = threading.Thread(target=long_job)
    t.start()
    held.wait(5)
    try:
        t0 = time.monotonic()
        r = client.get("/api/analytics/kpis", headers=h)
        took = time.monotonic() - t0
    finally:
        done.set()
        t.join()
    assert r.status_code == 200, r.text
    assert took < 3.0  # bounded by the request-audit lock wait, not the long job (or the 5 s writer timeout)
    assert audit_fallback.status(db)["unreconciled"] >= 1
    assert audit_fallback.reconcile(db)["remaining"] == 0


def test_sweep_scans_open_cases_without_holding_the_writer_lock(db, monkeypatch, new_case):
    """Pass 1 of the SLA sweep only reads: the first decision for every case is made while no write lock is held
    (before, the lock was taken at the first escalation and held while every later case was evaluated)."""
    from datetime import timedelta

    from kareshafaa.models import utcnow
    from kareshafaa.services import escalation

    ids = [new_case()["id"], new_case()["id"]]  # both overdue at a far-future "now": both get escalations

    seen: dict[int, bool] = {}
    real = escalation._decision

    def spy(case, step, now, sla):
        seen.setdefault(case.id, bool(db.info.get("writer_lock")))
        return real(case, step, now, sla)

    monkeypatch.setattr(escalation, "_decision", spy)
    out = escalation.run_sweep(db, now=utcnow() + timedelta(days=400), case_ids=ids)
    assert out["reminders"] + out["supervisor"] + out["clinical_lead"] + out["director"] >= 2  # real writes happened
    assert set(seen) == set(ids) and not any(seen.values())
    db.rollback()


def test_system_page_shows_alert_markup(client, as_user):
    r = client.get("/admin/system", headers=as_user("admin"))
    assert r.status_code == 200 and "js/admin_system.js" in r.text
    js = client.get("/static/js/admin_system.js").text
    assert "waiting to be added to the audit trail" in js and "/api/admin/system/audit-fallback/reconcile" in js
