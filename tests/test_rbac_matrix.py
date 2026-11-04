"""Permission-leak tests: every route x every role (8 roles + not signed in) against the intended policy.

The route list comes from the app's router (tests/rbac_routes.py), so a new route fails here until it is classified.
Allowed requests are sent with a body that cannot validate (``[]``) or with ids that do not exist, so nothing is
changed; denied requests are sent with real ids so a refusal cannot be a 404 by accident."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from kareshafaa.security import PERMISSIONS, PROGRAMME_WIDE_ROLES, ROLES
from tests.rbac_routes import POLICY, allowed_roles, flat_routes, route_perms

ROLE_USER = {"coordinator": "aisha.rahman", "gp": "omar.haddad", "specialist": "vikram.mehta",
             "clinical_lead": "fatima.al.mansoori", "analyst": "analyst", "admin": "admin", "translator": "translator",
             "patient": "patient.en"}
ACTORS = sorted(ROLE_USER) + ["anonymous"]
# allowed-role calls that would change something even with an invalid body / unknown id: only the refusals are sent
SKIP_ALLOWED = {"POST /api/auth/logout", "POST /logout", "POST /api/analytics/simulation/run",
                "POST /api/governance/proposals/generate", "POST /api/me/teach-back/reopen/cancel",
                "POST /api/admin/system/audit-fallback/reconcile"}
NO_SUCH = 999_999
# handler-checked routes with a body: refused roles must get past validation so the handler's own check is exercised
VALID_BODY = {"POST /api/signin-help/{request_id}/close": {"outcome": "closed", "note": "access check"}}


def _routes():
    from kareshafaa.main import create_app
    return {f"{m} {p}": r for m, p, r in flat_routes(create_app())}


ROUTES = _routes()


def test_every_route_is_classified():
    missing = sorted(set(ROUTES) - set(POLICY))
    stale = sorted(set(POLICY) - set(ROUTES))
    assert not missing, f"new routes need an access policy in tests/rbac_routes.py: {missing}"
    assert not stale, f"policy for routes that no longer exist: {stale}"


@pytest.mark.parametrize("key", sorted(POLICY))
def test_route_level_permission_matches_policy(key):
    pol, got = POLICY[key], route_perms(ROUTES[key].dependant)
    if pol.startswith("perm:"):
        assert got == [pol[5:]], (key, got)
    else:
        assert got == [], (key, got)  # inner / roles / page / public routes carry no route-level permission


def test_role_separation_in_the_permission_table():
    clinical = (PERMISSIONS["coordinator"] | PERMISSIONS["gp"] | PERMISSIONS["specialist"]) - {"sweep:run"}  # deadline check: shared tool
    admin_only = {"user:admin", "config:read", "audit:verify", "model:train", "sweep:run", "proposal:generate",
                  "simulation:run"} - PERMISSIONS["coordinator"]  # coordinators run the deadline check themselves
    assert set(ROLES) == set(ROLE_USER)
    assert not (PERMISSIONS["admin"] & (clinical | {"case:reopen", "proposal:decide", "model:approve", "content:approve"})), \
        "admin must have no clinical action"
    assert not (PERMISSIONS["clinical_lead"] & admin_only), "clinical lead must have no administration tool"
    assert PERMISSIONS["patient"] == {"self:read", "self:respond"}
    assert PERMISSIONS["translator"] == {"content:read", "content:translation_review"}
    assert all(p.endswith(":read") for p in PERMISSIONS["analyst"]), "analyst is read-only"
    assert PROGRAMME_WIDE_ROLES == {"coordinator", "clinical_lead"}
    assert {r for r in ROLES if "results:inbox" in PERMISSIONS[r]} == {"coordinator"}


@pytest.fixture(scope="module")
def ids(seeded):
    from kareshafaa.db import session_factory
    from kareshafaa.models import Case, GpSummary

    db = session_factory()()
    try:
        c = db.scalars(select(Case).order_by(Case.id)).first()
        sid = db.scalar(select(GpSummary.id).limit(1)) or 1
        return {"case_id": c.id, "patient_id": c.patient_id, "sid": sid}
    finally:
        db.close()


def _url(path: str, values: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), 1 if m.group(1) != "key" else "nope")), path)


def _detail(r) -> str:
    try:
        return str(r.json().get("detail", ""))
    except Exception:  # noqa: BLE001 - HTML page
        return r.text[:300]


@pytest.mark.parametrize("key", sorted(POLICY))
def test_http_access_matches_policy(key, client, as_user, ids):
    method, path = key.split(" ", 1)
    pol = POLICY[key]
    if pol == "public":
        if method == "GET":
            client.cookies.clear()
            r = client.get(_url(path, ids), follow_redirects=False)
            assert r.status_code not in (401, 403), (key, r.status_code)
        return
    route = ROUTES[key]
    has_body = bool(route.dependant.body_params)
    allowed = allowed_roles(pol)
    for actor in ACTORS:
        ok = actor in allowed
        if ok and key in SKIP_ALLOWED:
            continue
        values = dict(ids) if not ok else {**{k: NO_SUCH for k in ("uid", "recall_id", "appt_id", "request_id",
                                                                     "msg_id", "esc_id", "consult_id", "med_id",
                                                                     "proposal_id", "version")}, **ids}
        if ok and not has_body and method == "POST":
            values = {k: NO_SUCH for k in re.findall(r"\{(\w+)\}", path)}
        url = _url(path, values)
        if pol.startswith("roles:") and "signin-help" in path and method == "GET":
            url += "?audience=patient"
        h = as_user(ROLE_USER[actor]) if actor != "anonymous" else {}
        if actor == "anonymous":
            client.cookies.clear()
        kw = {"headers": h, "follow_redirects": False}
        if method == "POST":
            if not ok and has_body and pol.split(":")[0] in ("inner", "roles"):
                kw["json"] = VALID_BODY[key]  # KeyError = add a valid body for this handler-checked route
            else:
                kw["content"] = b"[]"
                kw["headers"] = {**h, "Content-Type": "application/json"}
        r = client.request(method, url, **kw)
        d = _detail(r)
        if pol.startswith("page:"):
            if actor == "anonymous":
                assert r.status_code == 303 and "/login" in r.headers["location"], (key, actor, r.status_code)
            else:
                assert r.status_code == (200 if ok else 403), (key, actor, r.status_code)
            continue
        if actor == "anonymous":
            assert r.status_code == 401, (key, r.status_code, d)
        elif ok:
            assert r.status_code != 401 and not (r.status_code == 403 and "lacks permission" in d), \
                (key, actor, r.status_code, d)
        else:
            assert r.status_code in (403, 404), (key, actor, r.status_code, d)
