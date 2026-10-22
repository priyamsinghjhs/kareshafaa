"""Public demo safety mode (KARESHAFAA_PUBLIC_DEMO=1) and proof that normal mode is unchanged."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kareshafaa import public_demo as pd

BLOCKED_CALLS = [
    ("post", "/api/governance/models/train", {}),
    ("post", "/api/governance/models/2/approve", {"rationale": "x"}),
    ("post", "/api/governance/models/2/reject", {"rationale": "x"}),
    ("post", "/api/governance/models/rollback", {"rationale": "x"}),
    ("post", "/api/governance/proposals/generate", {}),
    ("post", "/api/governance/proposals/1/decide", {"decision": "approve"}),
    ("post", "/api/governance/content/signoff", {}),
    ("post", "/api/sla/sweep", {}),
    ("post", "/api/analytics/simulation/run", {}),
    ("delete", "/api/cases/1", None),
    ("put", "/api/users/1", {"role": "admin"}),
    ("post", "/api/admin/reset", {}),
]


@pytest.fixture()
def demo_client(seeded, monkeypatch):
    """A second app instance built with KARESHAFAA_PUBLIC_DEMO=1 on the same synthetic test database."""
    from fastapi.testclient import TestClient

    from kareshafaa.config import get_settings
    from kareshafaa.main import create_app

    monkeypatch.setenv("KARESHAFAA_PUBLIC_DEMO", "1")
    monkeypatch.setenv("KARESHAFAA_DEMO_WRITE_LIMIT", "5")
    get_settings.cache_clear()
    try:
        yield TestClient(create_app())  # no lifespan: the nightly reset task is not started in tests
    finally:
        monkeypatch.delenv("KARESHAFAA_PUBLIC_DEMO")
        monkeypatch.delenv("KARESHAFAA_DEMO_WRITE_LIMIT")
        get_settings.cache_clear()


def _admin(c) -> dict:
    r = c.post("/api/auth/demo-login", json={"username": "admin"})
    assert r.status_code == 200, r.text
    c.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ------------------------------------------------------------------------------------------------ normal mode
def test_normal_mode_has_no_demo_banner_or_guard(client, as_user):
    from kareshafaa.config import get_settings

    assert get_settings().public_demo is False
    assert pd.BANNER not in client.get("/").text
    assert client.get("/healthz").json()["public_demo"] is False
    # admin-only endpoints keep their normal behaviour (here: unauthenticated -> 401, not the demo 403)
    r = client.post("/api/governance/models/train", json={})
    assert r.status_code == 401
    r = client.post("/api/governance/models/rollback", headers=as_user("aisha.rahman"), json={"rationale": "x"})
    assert r.status_code == 403 and r.json()["detail"] != pd.BLOCKED_DETAIL  # ordinary RBAC, not demo mode
    for _ in range(40):  # no write rate limit in normal mode
        assert client.post("/api/auth/logout").status_code == 200


# ------------------------------------------------------------------------------------------------ public demo
def test_public_demo_banner_and_health(demo_client):
    html = demo_client.get("/").text
    assert 'class="public-demo-banner"' in html and "Synthetic data demo, not for clinical use" in html
    assert demo_client.get("/healthz").json()["public_demo"] is True


@pytest.mark.parametrize("method,path,body", BLOCKED_CALLS)
def test_public_demo_refuses_destructive_admin_actions(demo_client, method, path, body):
    h = _admin(demo_client)
    kw = {"headers": h} if body is None else {"headers": h, "json": body}
    r = getattr(demo_client, method)(path, **kw)
    assert r.status_code == 403, (path, r.status_code, r.text)
    assert r.json()["detail"] == pd.BLOCKED_DETAIL


def test_public_demo_refusal_is_audited(demo_client, db):
    from sqlalchemy import select

    from kareshafaa.models import AuditEvent

    h = _admin(demo_client)
    assert demo_client.post("/api/governance/models/train", headers=h, json={}).status_code == 403
    db.expire_all()
    ev = db.scalars(select(AuditEvent).where(AuditEvent.path == "/api/governance/models/train")
                    .order_by(AuditEvent.id.desc()).limit(1)).first()
    assert ev is not None and ev.outcome != "success", ev and (ev.event, ev.outcome)


def test_public_demo_keeps_normal_clinical_reads_and_writes(demo_client):
    r = demo_client.post("/api/auth/demo-login", json={"username": "aisha.rahman"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    demo_client.cookies.clear()
    assert demo_client.get("/api/auth/me", headers=h).status_code == 200
    for path in ("/api/cases/1/transition", "/api/cases/1/notify", "/api/results", "/api/demo/scenarios/x"):
        assert not pd.is_blocked("POST", path), path


def test_public_demo_rate_limits_writes_per_session(demo_client):
    a = {"Authorization": "Bearer session-a-token"}
    b = {"Authorization": "Bearer session-b-token"}
    codes = [demo_client.post("/api/auth/logout", headers=a).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    r = demo_client.post("/api/auth/logout", headers=a)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert demo_client.post("/api/auth/logout", headers=b).status_code == 200  # other sessions unaffected
    assert demo_client.get("/api/auth/me", headers=a).status_code == 401  # reads are not rate limited


def test_rate_limiter_window():
    lim = pd.WriteRateLimiter(2, 10)
    assert lim.check("k", 0) == 0 and lim.check("k", 1) == 0
    assert lim.check("k", 2) == pytest.approx(8)
    assert lim.check("k", 10.5) == 0  # first hit expired


def test_client_key_prefers_session_then_forwarded_ip():
    assert pd.client_key({"authorization": "Bearer abc"}, {}, "1.2.3.4", "c") == "s:abc"
    assert pd.client_key({}, {"c": "tok"}, "1.2.3.4", "c") == "s:tok"
    assert pd.client_key({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, {}, "127.0.0.1", "c") == "ip:9.9.9.9"
    assert pd.client_key({}, {}, "1.2.3.4", "c") == "ip:1.2.3.4"


def test_reset_restores_demo_db_from_seed_copy(tmp_path):
    from kareshafaa.config import Settings

    live, seed = tmp_path / "demo.db", tmp_path / "seed.db"
    c = sqlite3.connect(live)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t (v TEXT)")
    c.execute("INSERT INTO t VALUES ('pristine')")
    c.commit()
    s = Settings(database_url=f"sqlite:///{live}", data_dir=tmp_path, model_dir=tmp_path / "m",
                 demo_seed_db=seed, secret_key="x")
    first = pd.reset_from_seed(s)  # no seed copy yet: it is created from the current DB, nothing restored
    assert first == {"reset": False, "seed_created": str(seed)} and seed.exists()
    c.execute("INSERT INTO t VALUES ('visitor change')")
    c.commit()
    out = pd.reset_from_seed(s)  # an open connection (like a running worker) sees the restored data
    assert out["reset"] is True
    assert [r[0] for r in c.execute("SELECT v FROM t")] == ["pristine"]
    c.close()


def test_reset_is_sqlite_only_and_noop_when_off(tmp_path, monkeypatch, capsys):
    from kareshafaa.config import Settings, get_settings

    s = Settings(database_url="postgresql://u@h/db", data_dir=tmp_path, model_dir=tmp_path / "m", secret_key="x")
    assert pd.reset_from_seed(s)["reset"] is False
    monkeypatch.delenv("KARESHAFAA_PUBLIC_DEMO", raising=False)
    get_settings.cache_clear()
    assert pd.main() == 0 and "public demo mode is off" in capsys.readouterr().out
    get_settings.cache_clear()


def test_next_nightly_reset_time_is_uae_local():
    uae = ZoneInfo("Asia/Dubai")
    assert pd.next_reset_at(datetime(2026, 10, 22, 1, 0, tzinfo=uae), 3) == datetime(2026, 10, 22, 3, 0, tzinfo=uae)
    assert pd.next_reset_at(datetime(2026, 10, 22, 3, 0, tzinfo=uae), 3) == datetime(2026, 10, 23, 3, 0, tzinfo=uae)
    assert os.environ.get("KARESHAFAA_PUBLIC_DEMO") is None
