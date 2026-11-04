"""Must never happen: a case in a contradictory state, or a successful change without an audit trail.

Property-style: Hypothesis drives random sequences of valid and invalid case actions through the HTTP API, by
random roles (allowed and not allowed), with random or broken bodies, interleaved with SLA sweeps and work-queue
reads. After every successful state-changing request there must be a request audit event (or a durable fallback
event) and, if the case itself changed, a domain audit event for that case. After each sequence the invariants hold
across ALL cases in the database (not just the ones touched), including the work queue's stored SLA thresholds."""
from __future__ import annotations

import random

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import func, select

from kareshafaa.statemachine import S
from tests.safety.invariants import audit_violations, case_violations
from tests.test_rbac_edges import ID_ROUTES, _body

pytestmark = pytest.mark.safety
USERS = ["aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori", "admin", "analyst", "patient.en"]
CASE_POSTS = sorted({p: (p, r) for m, p, r in ID_ROUTES if m == "POST" and p.startswith("/api/cases/{case_id}")
                     and p.count("{") == 1}.values(), key=lambda x: x[0])
STATES = [s.value for s in S]
CLOSURE = ["patient_declined_informed", "deceased", "moved_away", "screening_not_indicated", "x"]


def _snapshot(db, cid):
    from kareshafaa.models import Case
    db.expire_all()
    c = db.get(Case, cid)
    return (c.state, c.tier, c.owner_id, c.review_required, c.closed_at, c.closure_type, c.row_version)


def _heads(db):
    from kareshafaa.models import AuditEvent, AuditFallbackEvent
    return (db.scalar(select(func.max(AuditEvent.id))) or 0,
            db.scalar(select(func.count()).select_from(AuditFallbackEvent)))


def _make_body(path, route, rnd):
    b = _body(route)
    if b is None:
        return None
    if path.endswith("/transition"):
        b["to_state"] = rnd.choice(STATES)
    if "reason" in b and path.endswith("/close"):
        b["reason"] = rnd.choice(CLOSURE)
    for k in list(b):
        if isinstance(b[k], str) and rnd.random() < 0.3:
            b[k] = rnd.choice(["", "a reasoned clinical note", "x" * 3])
    if b and rnd.random() < 0.2:
        b.pop(rnd.choice(list(b)))  # an invalid body
    return b


ops = st.lists(st.tuples(st.integers(0, len(CASE_POSTS) + 1), st.integers(0, len(USERS) - 1), st.integers(0, 2**31)),
               min_size=8, max_size=25)


@settings(max_examples=6, deadline=None, suppress_health_check=list(HealthCheck), derandomize=True)
@given(seq=ops)
def test_random_action_sequences_keep_every_invariant(seq, client, as_user, new_case, db):
    from kareshafaa.models import AuditEvent
    cases = [new_case(value=250, smartphone=True, phone_valid=True)["id"], new_case("cervical", None, "HSIL")["id"]]
    unaudited = []
    for ri, ui, seed in seq:
        rnd = random.Random(seed)
        h = as_user(USERS[ui])
        cid = rnd.choice(cases)
        if ri == len(CASE_POSTS):
            r = client.post("/api/sla/sweep", headers=h, json={"case_ids": cases})
            url = "/api/sla/sweep"
        elif ri == len(CASE_POSTS) + 1:
            r = client.get("/api/queue?limit=200", headers=h)
            assert r.status_code in (200, 403)
            continue
        else:
            path, route = CASE_POSTS[ri]
            url = path.replace("{case_id}", str(cid))
            before, heads = _snapshot(db, cid), _heads(db)
            r = client.post(url, headers=h, json=_make_body(path, route, rnd))
        assert r.status_code < 500, (url, r.status_code, r.text[:300])
        if r.status_code >= 400 or url.endswith("/sweep"):
            continue
        db.expire_all()
        head, fb = _heads(db)
        logged = db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.id > heads[0], AuditEvent.event == "http.post", AuditEvent.path == url))
        if not logged and fb == heads[1]:
            unaudited.append(("request", url))
        if _snapshot(db, cid) != before:
            domain = db.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.id > heads[0], AuditEvent.case_id == cid, ~AuditEvent.event.like("http.%")))
            if not domain:
                unaudited.append(("case change", url))
    assert not unaudited, unaudited
    assert case_violations(db) == []
    assert audit_violations(db) == []


def test_invariants_hold_across_the_whole_database(db):
    assert case_violations(db) == []
