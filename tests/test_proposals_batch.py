"""Batched proposal lookups give exactly the proposals of the former per-candidate path, with fewer statements."""
from __future__ import annotations

from sqlalchemy import event, select

from kareshafaa.api.deps import actor_from_user
from kareshafaa.db import get_engine
from kareshafaa.models import RuleProposal, User
from kareshafaa.services.improve import generate_proposals

from proposal_oracle import generate_proposals_old


def _key(p: RuleProposal):
    return (p.kind, p.fingerprint, p.current_value, p.proposed_value, p.rationale, p.evidence, p.status)


def _run(db, fn):
    n = {"n": 0}

    def count(*_a, **_k):
        n["n"] += 1

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        out = [_key(p) for p in fn(db, actor_from_user(db.scalar(select(User).where(User.username == "admin"))))]
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)
    db.rollback()
    return out, n["n"]


def test_same_proposals_as_before_with_fewer_statements(db):
    old, n_old = _run(db, lambda d, a: (_mark(d), generate_proposals_old(d, a))[1])
    new, n_new = _run(db, lambda d, a: (_mark(d), generate_proposals(d, a))[1])
    assert old and new == old
    assert n_new < n_old


def _mark(db):
    """Clear pending proposals inside the transaction so the run has candidates to create (rolled back after)."""
    for p in db.scalars(select(RuleProposal).where(RuleProposal.status == "pending")):
        p.status = "rejected"
    db.flush()


def test_pending_duplicates_still_skipped(db):
    admin = actor_from_user(db.scalar(select(User).where(User.username == "admin")))
    _mark(db)
    first = generate_proposals(db, admin)
    assert first
    assert generate_proposals(db, admin) == []  # every candidate is now pending -> nothing new
    assert generate_proposals_old(db, admin) == []
    db.rollback()
