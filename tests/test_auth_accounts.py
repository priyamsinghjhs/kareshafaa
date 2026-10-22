"""Real per-user login: password hashing, sessions, CSRF, lockout, deactivation, admin user management, page and
API access per role, and proof that no API ever returns a password hash."""
from __future__ import annotations

import itertools
import logging
import os
import re
import stat
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kareshafaa import accounts

PW = "violet-canyon-harbour-73"
_n = itertools.count()


@pytest.fixture()
def fresh(app):
    """An isolated browser-like client (own cookie jar); no lifespan."""
    return TestClient(app)


@pytest.fixture()
def mkuser(db):
    """Create a user with a known password; returns its username."""
    from kareshafaa.models import Practitioner, User

    def _mk(role: str = "coordinator", password: str = PW, must_change: bool = False, active: bool = True) -> str:
        i = next(_n)
        pid = None
        if role in accounts.CLINICAL_ROLES:
            src = db.scalars(select(Practitioner).where(Practitioner.role == role, Practitioner.is_pool.is_(False))).first()
            p = Practitioner(name=f"Test {role} {i}", role=role, org_id=src.org_id)
            db.add(p)
            db.flush()
            pid = p.id
        u = User(username=f"t{i}.{role}", display_name=f"Test User {i}", role=role, practitioner_id=pid, active=active)
        accounts.set_password(u, password, must_change=must_change)
        db.add(u)
        db.commit()
        return u.username

    return _mk


def login(c: TestClient, username: str, password: str = PW, follow: bool = False):
    c.get("/login")
    tok = c.cookies.get("kareshafaa_csrf")
    return c.post("/login", data={"username": username, "password": password, "csrf_token": tok, "next": ""},
                  follow_redirects=follow)


def csrf_of(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["csrf"]


# ------------------------------------------------------------------------------------------------ hashing / policy
def test_passwords_are_argon2id_and_verify():
    h = accounts.hash_password(PW)
    assert h.startswith("$argon2id$") and PW not in h
    assert accounts.verify_password(h, PW) and not accounts.verify_password(h, PW + "x")
    assert not accounts.verify_password(None, PW)


@pytest.mark.parametrize("pw", ["short-pw-1", "password1234", "Password2026!", "qwertyuiop12", "aaaaaaaaaaaaaa",
                                "abcabcabcabcabc", "kareshafaa2026", "                "])
def test_password_policy_rejects_weak(pw):
    assert accounts.password_problems(pw), pw


def test_password_policy_accepts_passphrase_and_rejects_own_name():
    assert accounts.password_problems("violet canyon harbour") == []
    assert accounts.password_problems("aisha.rahman-is-great", "aisha.rahman", "Aisha Rahman")


def test_generated_passwords_pass_policy():
    assert all(not accounts.password_problems(accounts.generate_password()) for _ in range(20))


# ------------------------------------------------------------------------------------------------ login / logout
def test_login_page_and_unauthenticated_redirects(fresh):
    r = fresh.get("/login")
    assert r.status_code == 200 and 'name="password"' in r.text and 'autocomplete="current-password"' in r.text
    assert "demo-login" not in r.text and "role-switcher" not in r.text
    for path in ("/queue", "/cases/1", "/patient", "/dashboard", "/governance", "/audit", "/demo", "/ingest",
                 "/consults", "/overview", "/advanced", "/admin/users", "/account/password"):
        r = fresh.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), path
    for path in ("/api/queue", "/api/cases/1", "/api/admin/users", "/api/audit", "/api/me/case", "/api/auth/me"):
        assert fresh.get(path).status_code == 401, path


def test_password_login_sets_secure_session_cookie_and_logout(fresh, mkuser, db):
    from kareshafaa.models import AuditEvent

    u = mkuser("coordinator")
    r = login(fresh, u)
    assert r.status_code == 303 and r.headers["location"] == "/queue"
    sc = [h for k, h in r.headers.multi_items() if k == "set-cookie" and h.startswith("kareshafaa_session=")][0]
    assert "HttpOnly" in sc and "samesite=lax" in sc.lower() and "Max-Age" in sc
    me = fresh.get("/api/auth/me").json()
    assert me["role"] == "coordinator" and me["role_label"] == "Care coordinator" and me["csrf"]
    assert fresh.get("/queue").status_code == 200
    r = fresh.post("/logout", data={"csrf_token": me["csrf"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert fresh.get("/api/auth/me").status_code == 401
    db.expire_all()
    evs = {e.event for e in db.scalars(select(AuditEvent).where(AuditEvent.event.like("auth.%"))
                                       .order_by(AuditEvent.id.desc()).limit(20))}
    assert {"auth.login", "auth.logout"} <= evs


def test_session_cookie_is_secure_in_public_demo_mode(app, monkeypatch):
    from kareshafaa.api.auth import cookie_secure
    from kareshafaa.config import get_settings

    class R:
        class url:
            scheme = "http"
    monkeypatch.setattr(get_settings(), "public_demo", True)
    assert cookie_secure(R) is True
    monkeypatch.setattr(get_settings(), "public_demo", False)
    assert cookie_secure(R) is False
    R.url.scheme = "https"
    assert cookie_secure(R) is True


def test_generic_error_for_wrong_password_and_unknown_user(fresh, mkuser):
    u = mkuser()
    a = login(fresh, u, "wrong-password-123456")
    b = login(fresh, "no.such.user", "wrong-password-123456")
    assert a.status_code == b.status_code == 401
    assert "did not match" in a.text and "did not match" in b.text
    strip = lambda t: re.sub(r'value="[^"]*"', "", t)  # noqa: E731 - the echoed username and CSRF token differ
    assert strip(a.text) == strip(b.text)


def test_lockout_after_repeated_failures_then_audited(fresh, mkuser, db):
    from kareshafaa.models import AuditEvent

    u = mkuser()
    for _ in range(5):
        assert login(fresh, u, "not-the-password-999").status_code == 401
    r = login(fresh, u)  # correct password, but temporarily locked
    assert r.status_code == 429 and "paused" in r.text
    assert fresh.get("/api/auth/me").status_code == 401
    db.expire_all()
    fails = db.scalars(select(AuditEvent).where(AuditEvent.event == "auth.login_failed")).all()
    assert any(e.detail.get("username") == u and e.detail.get("reason") == "locked" for e in fails)
    assert all(e.outcome == "failure" for e in fails)


def test_lockout_expires_after_window(fresh, mkuser, db):
    from kareshafaa.models import LoginAttempt, User, utcnow

    u = mkuser()
    for _ in range(5):
        login(fresh, u, "not-the-password-999")
    assert login(fresh, u).status_code == 429
    db.expire_all()
    for a in db.scalars(select(LoginAttempt).where(LoginAttempt.username == u)):
        a.at = utcnow() - timedelta(minutes=30)
    db.scalar(select(User).where(User.username == u)).locked_until = utcnow() - timedelta(minutes=1)
    db.commit()
    assert login(fresh, u).status_code == 303


def test_ip_rate_limit_across_usernames(fresh, db, monkeypatch):
    from kareshafaa.config import get_settings
    from kareshafaa.models import LoginAttempt

    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 6)
    db.query(LoginAttempt).delete()
    db.commit()
    codes = [login(fresh, f"spray{i}.user", "guess-guess-guess").status_code for i in range(8)]
    assert codes[:6] == [401] * 6 and codes[6:] == [429, 429]


def test_login_requires_csrf_token(fresh, mkuser):
    u = mkuser()
    fresh.get("/login")
    r = fresh.post("/login", data={"username": u, "password": PW, "csrf_token": "forged"}, follow_redirects=False)
    assert r.status_code == 403
    r = fresh.post("/api/auth/login", json={"username": u, "password": PW})
    assert r.status_code == 403
    r = fresh.post("/api/auth/login", json={"username": u, "password": PW},
                   headers={"X-CSRF-Token": fresh.cookies.get("kareshafaa_csrf")})
    assert r.status_code == 200 and r.json()["next"] == "/queue"
    assert "$argon2" not in r.text


def test_cookie_session_writes_need_csrf_header(fresh, mkuser):
    u = mkuser("coordinator")
    login(fresh, u)
    tok = csrf_of(fresh)
    r = fresh.post("/api/cases/1/notify", json={"category": "reminder"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    r = fresh.post("/api/cases/1/notify", json={"category": "reminder"}, headers={"X-CSRF-Token": "x" * 40})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    r = fresh.post("/api/cases/1/notify", json={"category": "reminder"}, headers={"X-CSRF-Token": tok})
    assert "CSRF" not in r.text  # past the CSRF check (the domain rules decide the rest)
    assert fresh.post("/logout", data={}, follow_redirects=False).status_code == 403  # form logout needs it too


def test_session_idle_timeout(fresh, mkuser, db):
    from kareshafaa.models import User, UserSession, utcnow

    u = mkuser()
    login(fresh, u)
    assert fresh.get("/api/auth/me").status_code == 200
    db.expire_all()
    uid = db.scalar(select(User.id).where(User.username == u))
    for s in db.scalars(select(UserSession).where(UserSession.user_id == uid)):
        s.last_seen_at = utcnow() - timedelta(minutes=31)
    db.commit()
    assert fresh.get("/api/auth/me").status_code == 401
    assert fresh.get("/queue", follow_redirects=False).headers["location"].startswith("/login")


def test_session_token_stored_only_as_hash(fresh, mkuser, db):
    from kareshafaa.models import UserSession

    login(fresh, mkuser())
    tok = fresh.cookies.get("kareshafaa_session")
    db.expire_all()
    assert tok and tok.startswith("s1.")
    assert not db.scalar(select(UserSession).where(UserSession.token_hash == tok))
    assert all(tok not in (s.token_hash or "") for s in db.scalars(select(UserSession)))


def test_deactivated_user_cannot_log_in_and_is_signed_out(fresh, mkuser, as_user, client, db):
    from kareshafaa.models import User

    u = mkuser("gp")
    login(fresh, u)
    assert fresh.get("/api/auth/me").status_code == 200
    uid = db.scalar(select(User.id).where(User.username == u))
    r = client.post(f"/api/admin/users/{uid}/deactivate", json={"reason": "left the clinic"}, headers=as_user("admin"))
    assert r.status_code == 200 and r.json()["user"]["active"] is False
    assert fresh.get("/api/auth/me").status_code == 401
    r = login(fresh, u)
    assert r.status_code == 401 and "did not match" in r.text  # generic: does not reveal "deactivated"
    assert client.post(f"/api/admin/users/{uid}/activate", json={}, headers=as_user("admin")).status_code == 200
    assert login(fresh, u).status_code == 303


def test_password_hashes_never_returned_by_any_api(fresh, mkuser, as_user, client):
    u = mkuser("admin")
    login(fresh, u)
    h = as_user("admin")
    bodies = [fresh.get("/api/auth/me").text, client.get("/api/admin/users", headers=h).text,
              client.post("/api/admin/users", headers=h, json={"username": f"new.analyst{next(_n)}",
                                                               "display_name": "New Analyst", "role": "analyst"}).text]
    for path in ("/api/audit?limit=300&event=user.", "/api/audit?limit=300&event=auth.", "/api/cases/1",
                 "/api/directory/practitioners"):
        bodies.append(client.get(path, headers=h if "audit" in path else as_user("aisha.rahman")).text)
    for b in bodies:
        assert "$argon2" not in b and "password_hash" not in b and PW not in b


# ------------------------------------------------------------------------------------------------ admin users
def test_admin_creates_user_who_must_change_password(fresh, as_user, client):
    name = f"sara.ali{next(_n)}"
    r = client.post("/api/admin/users", headers=as_user("admin"),
                    json={"username": name, "display_name": "Sara Ali", "role": "analyst", "site": "HQ", "team": "Data"})
    assert r.status_code == 200, r.text
    temp = r.json()["temporary_password"]
    assert temp and not accounts.password_problems(temp) and r.json()["user"]["must_change_password"] is True
    r = login(fresh, name, temp)
    assert r.status_code == 303 and r.headers["location"] == "/account/password"
    assert fresh.get("/dashboard", follow_redirects=False).headers["location"] == "/account/password"
    assert fresh.get("/api/analytics/kpis").status_code == 403  # nothing else until the password is changed
    tok = csrf_of(fresh)
    r = fresh.post("/account/password", data={"csrf_token": tok, "current_password": temp, "new_password": "password1234",
                                              "confirm_password": "password1234"})
    assert r.status_code == 422 and "too common" in r.text
    new = "amber-meadow-lighthouse-8"
    r = fresh.post("/account/password", data={"csrf_token": tok, "current_password": temp, "new_password": new,
                                              "confirm_password": new})
    assert r.status_code == 200 and "has been changed" in r.text
    assert fresh.get("/api/analytics/kpis").status_code == 200
    fresh.cookies.clear()
    assert login(fresh, name, temp).status_code == 401
    assert login(fresh, name, new).headers["location"] == "/dashboard"


def test_admin_rules_role_reset_and_no_self_lockout(as_user, client, db, fresh, mkuser):
    from kareshafaa.models import User

    h = as_user("admin")
    assert client.post("/api/admin/users", headers=h, json={"username": "x.coord", "display_name": "X", "role": "coordinator"}).status_code in (400, 422)
    assert client.post("/api/admin/users", headers=h, json={"username": "x.pat", "display_name": "X", "role": "patient"}).status_code in (400, 422)
    assert client.post("/api/admin/users", headers=h, json={"username": "admin", "display_name": "Dup", "role": "analyst"}).status_code in (400, 422)
    assert client.post("/api/admin/users", headers=h, json={"username": "x.weak", "display_name": "X", "role": "analyst",
                                                            "temporary_password": "password1234"}).status_code in (400, 422)
    me = db.scalar(select(User).where(User.username == "admin"))
    assert client.post(f"/api/admin/users/{me.id}/deactivate", headers=h, json={}).status_code == 403
    assert client.post(f"/api/admin/users/{me.id}/role", headers=h, json={"role": "analyst"}).status_code == 403
    u = mkuser("analyst")
    uid = db.scalar(select(User.id).where(User.username == u))
    login(fresh, u)
    r = client.post(f"/api/admin/users/{uid}/role", headers=h, json={"role": "translator", "team": "Arabic"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "translator"
    assert fresh.get("/api/auth/me").status_code == 401  # a role change signs the user out
    r = client.post(f"/api/admin/users/{uid}/reset-password", headers=h, json={})
    temp = r.json()["temporary_password"]
    assert r.status_code == 200 and temp and r.json()["user"]["must_change_password"]
    assert login(fresh, u, PW).status_code == 401
    assert login(fresh, u, temp).headers["location"] == "/account/password"


def test_user_admin_events_are_audited(as_user, client, db):
    from kareshafaa.models import AuditEvent

    client.post("/api/admin/users", headers=as_user("admin"), json={"username": f"aud.{next(_n)}", "display_name": "A", "role": "analyst"})
    db.expire_all()
    evs = {e.event for e in db.scalars(select(AuditEvent).where(AuditEvent.event.like("user.%")))}
    assert {"user.create", "user.deactivate", "user.activate", "user.role_change", "user.password_reset"} & evs >= {"user.create"}


# ------------------------------------------------------------------------------------------------ RBAC: pages + APIs
PAGE_ACCESS = {  # page -> roles allowed
    "/queue": {"coordinator", "gp", "specialist", "clinical_lead"},
    "/cases/1": {"coordinator", "gp", "specialist", "clinical_lead"},
    "/patient": {"patient"},
    "/dashboard": {"clinical_lead", "analyst", "admin"},
    "/governance": {"clinical_lead", "analyst", "admin", "translator"},
    "/audit": {"clinical_lead", "admin"},
    "/demo": {"coordinator", "gp", "clinical_lead", "admin"},
    "/ingest": {"coordinator", "gp", "clinical_lead"},
    "/consults": {"coordinator", "gp", "specialist", "clinical_lead"},
    "/overview": {"clinical_lead"},
    "/advanced": {"clinical_lead", "admin"},
    "/admin/users": {"admin"},
    "/account/password": {"coordinator", "gp", "specialist", "clinical_lead", "analyst", "admin", "translator", "patient"},
}
ROLE_USER = {"coordinator": "aisha.rahman", "gp": "omar.haddad", "specialist": "vikram.mehta",
             "clinical_lead": "fatima.al.mansoori", "analyst": "analyst", "admin": "admin", "translator": "translator",
             "patient": "patient.en"}


@pytest.mark.parametrize("role", sorted(ROLE_USER))
def test_each_role_reaches_only_its_pages(role, client, as_user):
    h = as_user(ROLE_USER[role])
    for page, allowed in PAGE_ACCESS.items():
        r = client.get(page, headers=h, follow_redirects=False)
        assert r.status_code == (200 if role in allowed else 403), (role, page, r.status_code)
        if r.status_code == 403:
            assert "have access" in r.text


API_ACCESS = {  # GET endpoint -> roles allowed
    "/api/admin/users": {"admin"},
    "/api/audit?limit=1": {"clinical_lead", "admin"},
    "/api/queue?limit=1": {"coordinator", "gp", "specialist", "clinical_lead"},
    "/api/me/case": {"patient"},
    "/api/analytics/kpis": {"clinical_lead", "analyst", "admin"},
    "/api/governance/models": {"clinical_lead", "analyst", "admin"},
    "/api/governance/content": {"clinical_lead", "analyst", "translator"},
    "/api/ingest/messages": {"coordinator", "gp", "clinical_lead"},
    "/api/consults": {"coordinator", "gp", "specialist", "clinical_lead"},
}


@pytest.mark.parametrize("role", sorted(ROLE_USER))
def test_each_role_reaches_only_its_apis(role, client, as_user):
    h = as_user(ROLE_USER[role])
    for path, allowed in API_ACCESS.items():
        r = client.get(path, headers=h)
        if role in allowed:
            assert r.status_code == 200, (role, path, r.status_code, r.text[:200])
        else:
            assert r.status_code in (403, 404), (role, path, r.status_code)
    for path, body in (("/api/admin/users", {"username": "zz.top", "display_name": "Z", "role": "analyst"}),
                       ("/api/admin/users/1/reset-password", {}), ("/api/admin/users/2/deactivate", {})):
        if role != "admin":
            assert client.post(path, headers=h, json=body).status_code == 403, (role, path)


def test_role_home_pages_and_role_labels(fresh, mkuser):
    homes = {"coordinator": "/queue", "gp": "/queue?view=review", "clinical_lead": "/overview", "admin": "/admin/users",
             "analyst": "/dashboard", "translator": "/governance?tab=content"}
    for role, home in homes.items():
        fresh.cookies.clear()
        r = login(fresh, mkuser(role))
        assert r.headers["location"] == home, role
        page = fresh.get(home)
        assert page.status_code == 200
        if role == "gp":
            assert "Family doctor (GP)" in page.text


def test_nav_is_role_aware_and_short(client, as_user):
    from kareshafaa.ui import NAV

    for role, items in NAV.items():
        assert 1 <= len(items) <= 5, role
    html = client.get("/queue", headers=as_user("aisha.rahman")).text
    assert "Today&#39;s queue" in html or "Today's queue" in html
    assert 'href="/admin/users"' not in html and 'href="/audit"' not in html and "Run SLA sweep" not in html
    assert "Synthetic data demo, not for clinical use" not in html  # normal mode: tidy prototype strip instead


# ------------------------------------------------------------------------------------------------ demo accounts
def test_demo_provisioning_env_password_weak_or_blank_refused_in_public_mode(db, monkeypatch, caplog):
    from kareshafaa.config import get_settings
    from kareshafaa.models import User

    s = get_settings()
    monkeypatch.setattr(s, "public_demo", True)
    for k in [k for k in os.environ if k.startswith("KARESHAFAA_DEMO_PASSWORD")]:
        monkeypatch.delenv(k)
    for bad in (None, "", "password1234", "short"):
        monkeypatch.setattr(s, "demo_password", bad)
        with caplog.at_level(logging.WARNING, logger="kareshafaa.accounts"):
            out = accounts.provision_demo_accounts(db, s)
        assert out["provisioned"] == 0 and out["unusable"] >= 8 and "NOT USABLE" in caplog.text
        db.expire_all()
        assert db.scalar(select(User).where(User.username == "aisha.rahman")).password_hash is None
        caplog.clear()
    monkeypatch.setattr(s, "demo_password", "ocean-pebble-violin-2026")
    out = accounts.provision_demo_accounts(db, s)
    assert out["unusable"] == 0 and out["provisioned"] >= 8
    db.expire_all()
    u = db.scalar(select(User).where(User.username == "aisha.rahman"))
    assert accounts.verify_password(u.password_hash, "ocean-pebble-violin-2026") and u.is_demo_account
    assert u.must_change_password is False
    assert accounts.provision_demo_accounts(db, s)["changed"] == 0  # idempotent


def test_demo_provisioning_random_writes_private_file_outside_repo(db, monkeypatch, tmp_path):
    from kareshafaa.config import BASE_DIR, get_settings
    from kareshafaa.models import User

    s = get_settings()
    f = tmp_path / "creds.txt"
    monkeypatch.setattr(s, "public_demo", False)
    monkeypatch.setattr(s, "demo_password", None)
    monkeypatch.setattr(s, "demo_credentials_file", f)
    for k in [k for k in os.environ if k.startswith("KARESHAFAA_DEMO_PASSWORD")]:
        monkeypatch.delenv(k)
    for u in db.scalars(select(User).where(User.username.in_(accounts.DEMO_ACCOUNTS))):
        u.password_hash = None
    db.commit()
    out = accounts.provision_demo_accounts(db, s)
    assert out["mode"] == "random" and f.exists() and stat.S_IMODE(os.stat(f).st_mode) == 0o600
    rows = [ln.split("\t") for ln in f.read_text().splitlines() if ln and not ln.startswith("#")]
    assert {r[0] for r in rows} >= {"aisha.rahman", "omar.haddad", "admin", "patient.en"}
    db.expire_all()
    u = db.scalar(select(User).where(User.username == rows[0][0]))
    assert accounts.verify_password(u.password_hash, rows[0][2]) and not accounts.password_problems(rows[0][2])
    default = accounts.credentials_path(s.model_copy(update={"demo_credentials_file": None, "env": "dev"}))
    assert BASE_DIR.resolve() not in default.resolve().parents


def test_no_demo_password_in_repo():
    from kareshafaa.config import BASE_DIR

    pat = re.compile(r"KARESHAFAA_DEMO_PASSWORD\s*[=:]\s*['\"]?[A-Za-z0-9!@#%._-]{8,}")
    for p in list(BASE_DIR.joinpath("kareshafaa").rglob("*.py")) + [BASE_DIR / "render.yaml", BASE_DIR / "Dockerfile"]:
        assert not pat.search(p.read_text(errors="ignore")), p
    ry = (BASE_DIR / "render.yaml").read_text()
    assert "KARESHAFAA_DEMO_PASSWORD" in ry and "sync: false" in ry


def test_open_redirect_is_refused(fresh, mkuser):
    u = mkuser("coordinator")
    fresh.get("/login")
    r = fresh.post("/login", data={"username": u, "password": PW, "csrf_token": fresh.cookies.get("kareshafaa_csrf"),
                                   "next": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/queue"


def test_pages_and_api_are_not_cacheable(client, as_user):
    h = as_user("aisha.rahman")
    assert "no-store" in client.get("/queue", headers=h).headers["cache-control"]
    assert "no-store" in client.get("/api/queue?limit=1", headers=h).headers["cache-control"]
