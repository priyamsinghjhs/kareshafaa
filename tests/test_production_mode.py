"""Production mode (KARESHAFAA_MODE=production): a full working system - no showcase wording, persistent data,
first-start bootstrap, user management, abuse-level write limit, model files restorable from the database."""
from __future__ import annotations

import logging
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from kareshafaa import accounts

PW = "violet-canyon-harbour-73"
FOOTER = "KareShafaa is not a certified medical device. Clinical decisions stay with the treating clinician."
SHOWCASE = re.compile(r"(?i)\bdemo|synthetic")


@pytest.fixture()
def prod(seeded, monkeypatch):
    """The test settings object switched to production mode (restored afterwards)."""
    from kareshafaa.config import get_settings

    s = get_settings()
    for k, v in {"mode": "production", "env": "prod", "demo_logins": False, "public_demo": False,
                 "write_limit_per_minute": 4, "cookie_secure": False}.items():
        monkeypatch.setattr(s, k, v)
    return s


def _login(c, username, password=PW):
    c.get("/login")
    return c.post("/login", data={"username": username, "password": password,
                                  "csrf_token": c.cookies.get("kareshafaa_csrf"), "next": ""}, follow_redirects=False)


def test_database_url_formats_and_mode_settings(monkeypatch):
    from kareshafaa.config import Settings, get_settings, normalise_database_url

    assert normalise_database_url("postgres://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert normalise_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("sqlite:///x.db") == "sqlite:///x.db"
    s = get_settings()
    assert s.mode == "standard" and isinstance(Settings().mode, str)


def test_production_settings_are_derived(monkeypatch):
    from kareshafaa import config

    monkeypatch.setenv("KARESHAFAA_MODE", "production")
    monkeypatch.setenv("KARESHAFAA_DEMO_LOGINS", "true")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/x")
    monkeypatch.delenv("KARESHAFAA_DATABASE_URL")
    s = config.derive(config.Settings())
    assert (s.mode, s.env, s.demo_logins, s.public_demo) == ("production", "prod", False, False)
    assert s.database_url == "postgresql+psycopg://u:p@db.example/x" and config.storage_is_persistent(s)
    monkeypatch.setenv("KARESHAFAA_MODE", "public_demo")
    assert config.derive(config.Settings()).public_demo is True


def test_storage_persistence_detection(monkeypatch):
    from kareshafaa.config import get_settings, storage_is_persistent

    s = get_settings()
    monkeypatch.setenv("RENDER", "true")
    assert storage_is_persistent(s) is False  # SQLite on a Render instance disk
    monkeypatch.setattr(s, "storage_persistent", True)
    assert storage_is_persistent(s) is True
    monkeypatch.delenv("RENDER")
    monkeypatch.setattr(s, "storage_persistent", None)
    assert storage_is_persistent(s) is True


def test_production_app_full_working_system(prod, db):
    from kareshafaa.main import create_app
    from kareshafaa.models import User

    for u in db.scalars(select(User).where(User.username.in_(["admin", "aisha.rahman", "fatima.al.mansoori"]))):
        accounts.set_password(u, PW)
    db.commit()
    c = TestClient(create_app())
    assert c.post("/api/auth/demo-login", json={"username": "admin"}).status_code == 404
    assert c.get("/healthz").json()["mode"] == "production"
    page = c.get("/login").text
    assert FOOTER in page and not SHOWCASE.search(page)
    assert _login(c, "admin").headers["location"] == "/admin/users"
    h = {"X-CSRF-Token": c.get("/api/auth/me").json()["csrf"]}
    r = c.post("/api/admin/users", headers=h, json={"username": "prod.analyst", "display_name": "Prod Analyst", "role": "analyst"})
    assert r.status_code == 200 and r.json()["temporary_password"]  # user management works in production
    sysinfo = c.get("/api/admin/system").json()
    assert sysinfo["mode"] == "production" and "password" not in str(sysinfo).lower()
    for path in ("/admin/users", "/admin/system", "/advanced", "/dashboard"):
        html = c.get(path).text
        assert FOOTER in html and not SHOWCASE.search(html), path
    assert 'href="/walkthrough"' not in c.get("/advanced").text  # walkthrough off in production
    assert c.post("/api/demo/scenarios/fatima_hl7_rejection", headers=h, json={}).status_code in (403, 404)
    # own password change works (not refused as in the read-only showcase)
    r = c.post("/api/auth/password", headers=h, json={"current_password": PW, "new_password": "quartz-lagoon-cedar-88"})
    assert r.status_code == 200, r.text


def test_production_write_limit_per_user(prod, db):
    from kareshafaa.main import create_app
    from kareshafaa.models import User

    u = db.scalar(select(User).where(User.username == "aisha.rahman"))
    accounts.set_password(u, PW)
    db.commit()
    c = TestClient(create_app())
    _login(c, "aisha.rahman")
    h = {"X-CSRF-Token": c.get("/api/auth/me").json()["csrf"]}
    codes = [c.post("/api/cases/1/notify", headers=h, json={"category": "reminder"}).status_code for _ in range(5)]
    assert codes[4] == 429 and 429 not in codes[:4]
    r = c.post("/api/cases/1/notify", headers=h, json={"category": "reminder"})
    assert "demo" not in r.text.lower() and int(r.headers["retry-after"]) >= 1
    assert c.get("/api/queue?limit=1").status_code == 200  # reads are never limited


def test_production_provisioning_never_overwrites_passwords(prod, db, monkeypatch, caplog):
    from kareshafaa.models import User

    users = {u.username: u for u in db.scalars(select(User).where(User.username.in_(accounts.DEMO_ACCOUNTS)))}
    users["admin"].password_hash = None
    accounts.set_password(users["aisha.rahman"], "changed-by-the-user-2026")
    db.commit()
    monkeypatch.setattr(prod, "demo_password", "first-password-for-staff-9")
    out = accounts.provision_demo_accounts(db, prod)
    assert out["mode"] == "production" and out["unusable"] == 0
    db.expire_all()
    assert accounts.verify_password(db.scalar(select(User).where(User.username == "admin")).password_hash,
                                    "first-password-for-staff-9")
    assert accounts.verify_password(db.scalar(select(User).where(User.username == "aisha.rahman")).password_hash,
                                    "changed-by-the-user-2026")  # kept across restarts
    # weak/missing env password: existing passwords are kept, nothing is wiped, a warning is logged
    db.scalar(select(User).where(User.username == "analyst")).password_hash = None
    db.commit()
    monkeypatch.setattr(prod, "demo_password", "short")
    with caplog.at_level(logging.WARNING, logger="kareshafaa.accounts"):
        out = accounts.provision_demo_accounts(db, prod)
    assert out["unusable"] == 1 and "WITHOUT A PASSWORD" in caplog.text
    db.expire_all()
    assert db.scalar(select(User).where(User.username == "aisha.rahman")).password_hash


@pytest.mark.parametrize("user,path", [("aisha.rahman", "/queue"), ("omar.haddad", "/queue?view=review"),
                                       ("fatima.al.mansoori", "/overview"), ("admin", "/admin/users"),
                                       ("patient.en", "/patient"), ("fatima.al.mansoori", "/walkthrough")])
def test_pages_have_footer_and_no_showcase_wording(client, as_user, user, path):
    html = client.get(path, headers=as_user(user)).text
    assert FOOTER in html and "Synthetic data demo" not in html
    visible = re.sub(r"<script.*?</script>|<[^>]+>", " ", html, flags=re.S)  # text people read (not URLs/ids)
    assert not SHOWCASE.search(visible), (path, SHOWCASE.search(visible).group(0))


def test_walkthrough_page_moved_and_restricted(client, as_user):
    assert client.get("/demo", headers=as_user("admin")).status_code == 404
    assert client.get("/walkthrough", headers=as_user("fatima.al.mansoori")).status_code == 403  # admin tool, not a clinical duty
    assert client.get("/walkthrough", headers=as_user("admin")).status_code == 200
    for u in ("aisha.rahman", "omar.haddad", "analyst"):
        assert client.get("/walkthrough", headers=as_user(u)).status_code == 403


def test_admin_system_page_admin_only_and_storage_note(client, as_user, monkeypatch):
    assert client.get("/api/admin/system", headers=as_user("aisha.rahman")).status_code == 403
    monkeypatch.setenv("RENDER", "true")
    r = client.get("/api/admin/system", headers=as_user("admin")).json()
    assert r["storage_persistent"] is False and "lost" in r["storage_note"] and "demo" not in str(r).lower()
    assert client.get("/admin/system", headers=as_user("admin")).status_code == 200


def test_manifest_and_offline_page_have_no_showcase_wording(client):
    m = client.get("/manifest.webmanifest").text
    off = client.get("/offline").text
    assert not SHOWCASE.search(m) and not SHOWCASE.search(off) and FOOTER in off


def test_model_file_restored_from_database_copy(db, tmp_path):
    from kareshafaa.ml import registry
    from kareshafaa.models import ModelArtifact, ModelVersion

    mv = registry.production(db) or db.scalars(select(ModelVersion)).first()
    registry.store_artifact(db, mv)
    db.commit()
    assert db.get(ModelArtifact, mv.artifact_sha256) is not None
    original = mv.artifact_path
    mv.artifact_path = str(tmp_path / "gone" / "model.joblib")  # a fresh container: the file is not there
    try:
        assert registry.load_artifact(mv) is not None
        assert (tmp_path / "gone" / "model.joblib").exists()
    finally:
        mv.artifact_path = original
        db.rollback()


def test_bootstrap_copies_into_empty_database_once_and_keeps_data(seeded, tmp_path):
    """First start on an empty database copies every row (audit chain intact); a restart keeps the data."""
    from kareshafaa import bootstrap
    from kareshafaa.config import get_settings
    from kareshafaa.db import Base

    src = get_settings().database_url.replace("sqlite:///", "")
    target = tmp_path / "fresh.db"
    eng = create_engine(f"sqlite:///{target}")
    from kareshafaa import models  # noqa: F401
    Base.metadata.create_all(eng)
    assert bootstrap._is_empty(eng)
    counts = bootstrap.copy_database(__import__("pathlib").Path(src), eng)
    assert counts["audit_events"] > 0 and not bootstrap._is_empty(eng)
    a, b = sqlite3.connect(src), sqlite3.connect(target)
    for t in ("patients", "cases", "audit_events", "users", "diagnostic_reports"):
        assert a.execute(f"select count(*) from {t}").fetchone() == b.execute(f"select count(*) from {t}").fetchone()
    assert a.execute("select max(hash) from audit_events").fetchone() == b.execute("select max(hash) from audit_events").fetchone()
    assert a.execute("select sum(case_id) from diagnostic_reports").fetchone() == \
        b.execute("select sum(case_id) from diagnostic_reports").fetchone()  # cyclic references filled in
    eng.dispose()
