"""Round 21: graceful degradation under overload, and per-database writer/sweeper locks.

Root cause of the demo-server hang (21 Oct 11:02 IST): the cross-process audit-writer lock file (and the SLA
sweeper election lock) lived in data_dir and was SHARED by every server/script using that directory, whatever its
database. A load test on another database therefore stalled the demo server's audited requests (reproduced: probe
requests to :8000 timed out at 20 s while :8001 was overloaded), and the load-test server never ran its own sweep."""
import threading
import time

import pytest
from sqlalchemy import select


def test_writer_lock_wait_is_bounded_and_answers_busy(db, monkeypatch):
    from wasl import audit
    from wasl.config import get_settings
    from wasl.services.errors import ServiceBusy

    monkeypatch.setattr(get_settings(), "writer_lock_timeout_s", 0.2)
    held = threading.Event()
    done = threading.Event()

    def holder():
        audit._WRITER_LOCK.acquire(file_lock=False)
        held.set()
        done.wait(5)
        audit._WRITER_LOCK.release(file_lock=False)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    try:
        t0 = time.monotonic()
        with pytest.raises(ServiceBusy):
            audit._WRITER_LOCK.acquire(file_lock=False, timeout=0.2)
        assert time.monotonic() - t0 < 1.0
    finally:
        done.set()
        t.join()
    audit._WRITER_LOCK.acquire(file_lock=False, timeout=0.2)  # free again afterwards: nothing leaked
    audit._WRITER_LOCK.release(file_lock=False)


def test_lock_files_are_per_database(tmp_path):
    from wasl.audit import lock_key

    a, b = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    assert lock_key(a) != lock_key(b)
    assert lock_key(a) == lock_key(str(tmp_path / "a.db"))


def test_busy_maps_to_503_with_retry_after(app, client, as_user, monkeypatch):
    from wasl.services import errors

    def boom(*_a, **_k):
        raise errors.ServiceBusy()

    from wasl.api import ops
    monkeypatch.setattr(ops.analytics, "cached", boom)
    r = client.get("/api/analytics/kpis", headers=as_user("analyst"))
    assert r.status_code == 503 and r.headers.get("Retry-After")


def test_admission_control_sheds_fast_and_audits_the_count(app, client, as_user, monkeypatch, db):
    from wasl.config import get_settings
    from wasl.models import AuditEvent

    h = as_user("analyst")
    monkeypatch.setattr(get_settings(), "max_inflight_requests", 0)
    t0 = time.monotonic()
    r = client.get("/api/analytics/kpis", headers=h)
    assert r.status_code == 503 and r.headers["Retry-After"] and time.monotonic() - t0 < 1.0
    monkeypatch.setattr(get_settings(), "max_inflight_requests", 24)
    assert client.get("/api/analytics/kpis", headers=h).status_code == 200
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "http.load_shed").order_by(AuditEvent.id.desc()))
    assert ev is not None and ev.detail["requests_shed"] >= 1
