"""Work queue in SQL (stored SLA thresholds) == the former Python path (tests/queue_oracle.py).

Compared on the seeded synthetic database for every staff user who can read the queue, every view, programme and
search filters, paging, and several 'now' times (so green → amber → red transitions are exercised)."""
from __future__ import annotations

import itertools
import random
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from kareshafaa.api.deps import actor_from_user
from kareshafaa.models import Case, User, utcnow
from kareshafaa.queue_store import ALWAYS, refresh, thresholds
from kareshafaa.security import has_perm
from kareshafaa.services.queue import VIEWS, sort_key, work_queue
from kareshafaa.sla import rag_status

from queue_oracle import work_queue_python

BIG = 100_000


def _assert_same(old: dict, new: dict, ctx) -> None:
    assert new["counts"] == old["counts"], ctx
    assert new["total"] == old["total"], ctx
    o, n = old["items"], new["items"]
    assert [sort_key(r) for r in n] == [sort_key(r) for r in o], ctx
    # identical rows; within exact sort-key ties the old order was the database's scan order, now case id
    assert sorted(n, key=lambda r: r["id"]) == sorted(o, key=lambda r: r["id"]), ctx
    for k, grp in itertools.groupby(n, key=sort_key):
        ids = [r["id"] for r in grp]
        assert ids == sorted(ids), ctx


def _queue_users(db):
    return [actor_from_user(u) for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.id))
            if has_perm(u.role, "queue:read") and u.role != "patient"]


@pytest.fixture(scope="module")
def nows():
    t = utcnow()
    return [t, t - timedelta(days=3), t + timedelta(hours=20), t + timedelta(days=9), t + timedelta(days=60)]


def test_sql_queue_matches_python_all_users_views_times(db, nows):
    users = _queue_users(db)
    assert len(users) >= 5
    checked = 0
    for i, now in enumerate(nows):
        for a in users:
            views = VIEWS if i < 2 else ("all", "overdue", "review", "mine")
            for v in views:
                old = work_queue_python(db, a, v, now=now, limit=BIG)
                new = work_queue(db, a, v, now=now, limit=BIG)
                _assert_same(old, new, (a.display, a.role, v, now))
                checked += 1
    assert checked > 100


def test_sql_queue_matches_python_filters_and_paging(db, nows):
    users = {u.role: u for u in _queue_users(db)}
    rng = random.Random(5)
    some = db.execute(select(Case.id).where(Case.state != "closed").limit(3)).scalars().all()
    searches = [None, "SYN-", "a", "zzzz-no-match", f"#{some[0]}" if some else "#1", str(some[-1]) if some else "1"]
    for now in nows[:3]:
        for a in users.values():
            for prog in (None, "colorectal_fit", "cervical_hpv", "breast_mammo", "diabetes_hba1c"):
                for s in searches:
                    old = work_queue_python(db, a, "all", now=now, limit=BIG, programme=prog, search=s)
                    new = work_queue(db, a, "all", now=now, limit=BIG, programme=prog, search=s)
                    _assert_same(old, new, (a.role, prog, s, now))
            full = work_queue(db, a, "all", now=now, limit=BIG)
            old_full = work_queue_python(db, a, "all", now=now, limit=BIG)
            for _ in range(5):
                off, lim = rng.randint(0, max(1, full["total"])), rng.randint(1, 60)
                page = work_queue(db, a, "all", now=now, offset=off, limit=lim)
                assert page["items"] == full["items"][off:off + lim]
                assert [sort_key(r) for r in page["items"]] == [sort_key(r) for r in old_full["items"][off:off + lim]]
                assert page["total"] == old_full["total"] and page["counts"] == old_full["counts"]


def test_unknown_view_is_empty_like_before(db):
    a = _queue_users(db)[0]
    old, new = work_queue_python(db, a, "nope"), work_queue(db, a, "nope")
    assert new["items"] == old["items"] == [] and new["total"] == old["total"] == 0
    assert new["counts"] == old["counts"]


def test_thresholds_reproduce_rag_status_exactly():
    rng = random.Random(42)
    base = datetime(2026, 3, 1, 8, 0, 0)
    for _ in range(4000):
        start = base + timedelta(seconds=rng.randint(0, 10**7), microseconds=rng.randint(0, 999_999))
        due = start + timedelta(seconds=rng.choice([0, -5, 1, 3600, rng.randint(1, 4 * 10**6)]),
                                microseconds=rng.randint(0, 999_999))
        rf = rng.choice([0.0, 0.25, 0.3, 0.5, 0.65, 0.8, 1.0, 1.2, rng.random()])
        amber, red = thresholds(start, due, rf)
        probes = [start - timedelta(days=1), start, due, due - timedelta(microseconds=1), due + timedelta(microseconds=1),
                  amber - timedelta(microseconds=1), amber, red - timedelta(microseconds=1), red] + \
                 [start + timedelta(seconds=rng.uniform(-1000, 5 * 10**6)) for _ in range(6)]
        for t in probes:
            if t < datetime(1901, 1, 1):
                continue
            exp = rag_status(start, due, t, rf)[0]
            got = "red" if t >= red else "amber" if t >= amber else "green"
            assert got == exp, (start, due, rf, t, amber, red)
    assert thresholds(None, None, 0.5) == (None, None)
    assert thresholds(base, None, 0.5) == (None, None)
    assert thresholds(base, base, 0.5) == (ALWAYS, ALWAYS)


def test_stored_thresholds_current_after_seed_and_workflow(db):
    """The flush hook kept every case's stored thresholds current through the whole seeded history (replayed
    through the normal services: steps, tier reviews, escalations, closures, re-opens)."""
    import kareshafaa.queue_store as qs

    assert refresh(db.connection(), None, write=False) == 0, qs.last_stale[:5]
    db.rollback()


def test_stored_thresholds_follow_sla_rule_change(db):
    from kareshafaa.models import SlaRule, StepTask

    row = db.execute(select(Case.id, StepTask.state, Case.tier).join(StepTask, StepTask.case_id == Case.id)
                     .where(Case.state != "closed", StepTask.status == "in-progress", StepTask.due_at.is_not(None))
                     .limit(1)).one()
    cid, st, tier = row
    rule = db.scalar(select(SlaRule).where(SlaRule.state == st, SlaRule.tier == tier, SlaRule.active.is_(True)))
    before = db.scalar(select(Case.sla_amber_at).where(Case.id == cid))
    rule.reminder_fraction = 0.9 if rule.reminder_fraction < 0.9 else 0.2
    db.flush()
    after = db.scalar(select(Case.sla_amber_at).where(Case.id == cid))
    assert after != before
    assert refresh(db.connection(), None) == 0
    db.rollback()


def test_stored_thresholds_follow_step_changes(db):
    from kareshafaa.models import StepTask

    step = db.scalar(select(StepTask).join(Case, Case.id == StepTask.case_id)
                     .where(Case.state != "closed", StepTask.status == "in-progress", StepTask.due_at.is_not(None))
                     .limit(1))
    step.due_at = step.due_at + timedelta(days=2)  # e.g. a paused / re-timed clock
    db.flush()
    assert db.scalar(select(Case.sla_red_at).where(Case.id == step.case_id)) == step.due_at
    step.status, step.ended_at = "completed", utcnow()  # clock stopped, no new step yet
    db.flush()
    assert db.execute(select(Case.sla_amber_at, Case.sla_red_at).where(Case.id == step.case_id)).one() == (None, None)
    db.rollback()


def test_workflow_actions_keep_thresholds_current(client, as_user, new_case, db):
    """Create a case through the API, act on it, and check the stored thresholds after each change."""
    from kareshafaa.db import session_factory

    c = new_case()
    h = as_user("aisha.rahman")
    with session_factory()() as s:
        assert refresh(s.connection(), [c["id"]]) == 0
        assert s.scalar(select(Case.sla_red_at).where(Case.id == c["id"])) is not None
    acts = client.get(f"/api/cases/{c['id']}/actions", headers=h)
    if acts.status_code == 200:
        for a in (acts.json().get("actions") or [])[:3]:
            name = a.get("action") if isinstance(a, dict) else a
            client.post(f"/api/cases/{c['id']}/actions/{name}", headers=h, json={"note": "queue threshold check"})
            with session_factory()() as s:
                assert refresh(s.connection(), [c["id"]]) == 0
