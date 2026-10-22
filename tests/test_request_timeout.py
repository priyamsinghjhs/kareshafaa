"""Round 23: a request that runs forever is cut off (503 + Retry-After), its transaction is rolled back, it can never
save anything later, the audit chain stays intact, and the worker keeps serving. The hanging endpoint exists only in
this test."""
import threading
import time

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_hanging_request_is_cut_off_rolled_back_and_never_saves(app, client, as_user, monkeypatch, db, caplog):
    from kareshafaa import audit
    from kareshafaa.audit import verify_chain
    from kareshafaa.config import get_settings
    from kareshafaa.db import get_db, session_factory
    from kareshafaa.models import AuditEvent
    from kareshafaa.request_timeout import RequestTimedOut
    from kareshafaa.security import Actor

    monkeypatch.setattr(get_settings(), "request_timeout_s", 0.5)
    monkeypatch.setattr(get_settings(), "writer_lock_timeout_s", 2.0)
    release, finished = threading.Event(), threading.Event()
    outcome = {}
    marker = f"hang-{time.time()}"
    actor = Actor(user_id=None, role="system", display="test", is_human=False)

    def hang(db_: Session = Depends(get_db)) -> dict:
        try:
            audit.record(db_, actor, "C", "test.half_done_write", detail={"marker": marker})  # flush: writer lock held
            release.wait(15)  # stuck in Python code while holding the transaction
            db_.commit()
            outcome["result"] = "committed"
        except RequestTimedOut as e:
            outcome["result"] = f"refused: {e}"
        except Exception as e:  # noqa: BLE001
            outcome["result"] = f"error: {e!r}"
        finally:
            finished.set()
        return {"ok": True}

    app.router.add_api_route("/api/test-only/hang", hang, methods=["GET"])
    try:
        t0 = time.monotonic()
        r = client.get("/api/test-only/hang")
        elapsed = time.monotonic() - t0
        assert r.status_code == 503 and r.headers.get("Retry-After"), r.text
        assert "nothing was saved" in r.json()["detail"] and elapsed < 5
        logged = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "stack of the stuck request" in logged and "release.wait" in logged  # the stuck line is in the dump

        # the transaction was rolled back at cut-off: another writer gets the writer lock straight away
        s2 = session_factory()()
        try:
            t1 = time.monotonic()
            audit.record(s2, actor, "C", "test.other_writer", detail={"marker": marker})
            s2.commit()
            assert time.monotonic() - t1 < 1.5
        finally:
            s2.close()

        # the worker keeps serving
        assert client.get("/api/analytics/kpis", headers=as_user("analyst")).status_code == 200
        health = client.get("/healthz").json()
        assert health["request_timeouts"] >= 1

        release.set()  # the stuck code resumes and tries to commit: refused
        assert finished.wait(10)
        assert outcome["result"].startswith("refused"), outcome
    finally:
        release.set()
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/api/test-only/hang"]

    db.expire_all()
    rows = db.scalars(select(AuditEvent).where(AuditEvent.event.in_(("test.half_done_write", "test.other_writer")))).all()
    events = {e.event for e in rows if (e.detail or {}).get("marker") == marker}
    assert events == {"test.other_writer"}  # the half-done write was never saved
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "http.request_timeout",
                                              AuditEvent.path == "/api/test-only/hang")) is not None
    chain = verify_chain(db)
    assert chain["valid"], chain
