"""Explicit human sign-off on every high-impact action (wasl/signoff.py)."""
import importlib
import inspect

import pytest
from sqlalchemy import select

from wasl.models import Case, Escalation, ModelVersion, RuleProposal
from wasl.security import ML_SERVICE, SYSTEM, Actor
from wasl.services.errors import Forbidden, Invalid
from wasl.signoff import HIGH_IMPACT, require_signoff

# automated actors, including a "bot" that carries a clinical lead's role: none may take a high-impact action
BOTS = [SYSTEM, ML_SERVICE, Actor(user_id=None, role="clinical_lead", display="script with lead role", is_human=False),
        Actor(user_id=7, role="clinical_lead", display="integration job", is_human=False)]


def _fn(where):
    mod, name = where.split(":")
    return getattr(importlib.import_module(mod), name)


@pytest.mark.parametrize("action", sorted(HIGH_IMPACT))
def test_every_high_impact_function_calls_the_guard(action):
    src = inspect.getsource(_fn(HIGH_IMPACT[action].where))
    assert "require_signoff(" in src
    assert f'"{action}"' in src or (action.startswith("case.close.") and '"case.close.authorised"' in src)


@pytest.mark.parametrize("action", sorted(HIGH_IMPACT))
def test_guard_refuses_bots_missing_permission_and_missing_rationale(action):
    spec = HIGH_IMPACT[action]
    for bot in BOTS:
        with pytest.raises(Forbidden, match="named person"):
            require_signoff(bot, action, "a long enough rationale")
    with pytest.raises(Forbidden):
        require_signoff(Actor(user_id=1, role="analyst", display="analyst"), action, "a long enough rationale")
    role = next(r for r in ("clinical_lead", "gp", "specialist", "coordinator", "translator")
                if any(Actor(user_id=1, role=r, display="x").can(p) for p in spec.perms))
    human = Actor(user_id=1, role=role, display="Named Person")
    if spec.min_note:
        with pytest.raises(Invalid):
            require_signoff(human, action, "")
        with pytest.raises(Invalid):
            require_signoff(human, action, "x" * (spec.min_note - 1))
    require_signoff(human, action, "x" * max(spec.min_note, 1))


def _any_case(db, **flt):
    q = select(Case)
    for k, v in flt.items():
        q = q.where(getattr(Case, k) == v)
    return db.scalars(q.limit(1)).first()


def test_service_functions_refuse_bots_before_any_change(db):
    from wasl.ml import registry
    from wasl.services import escalation, improve, ingest
    from wasl.services import workflow as wf

    open_case = _any_case(db, state="appointment_booked") or db.scalar(select(Case).where(Case.state != "closed"))
    closed = _any_case(db, state="closed")
    esc = db.scalar(select(Escalation).limit(1))
    prop = db.scalar(select(RuleProposal).limit(1))
    mv = db.scalar(select(ModelVersion).limit(1))
    before = (open_case.state, open_case.owner_id, open_case.tier, closed.state)
    lead_bot = BOTS[2]
    calls = [
        lambda a: wf.close_case(db, open_case, a, "authorised_decision", reason="moved_away", note="moved abroad permanently"),
        lambda a: wf.transition(db, closed, "triaged", a, note="re-open: new information arrived"),
        lambda a: wf.assign(db, open_case, open_case.owner_id, a, "cover"),
        lambda a: wf.request_transfer(db, open_case, open_case.owner_id, a, "moving to another clinic"),
        lambda a: wf.decide_transfer(db, open_case, a, True, "ok accepted"),
        lambda a: wf.record_confirmatory_result(db, open_case, a, "negative"),
        lambda a: wf.decide_referral(db, open_case, a, False, reason_code="other", note="not appropriate"),
        lambda a: wf.start_care(db, open_case, a, "surveillance", "surveillance plan"),
        lambda a: escalation.acknowledge_escalation(db, esc, a, "seen, calling now"),
        lambda a: registry.approve(db, mv.version, a, "looks good to me"),
        lambda a: registry.reject(db, mv.version, a, "not good enough"),
        lambda a: registry.rollback(db, a, "drift detected on region"),
        lambda a: improve.decide_proposal(db, prop.id if prop else 1, a, True, "approved by lead"),
        lambda a: ingest.resolve_match(db, 1, open_case.patient_id, a),
    ]
    for call in calls:
        for bot in (SYSTEM, lead_bot):
            with pytest.raises(Forbidden):
                call(bot)
    db.rollback()
    db.expire_all()
    assert (open_case.state, open_case.owner_id, open_case.tier, closed.state) == before


def test_reopen_needs_human_with_permission_and_reason(client, as_user, db):
    closed = _any_case(db, state="closed")
    lead = as_user("fatima.al.mansoori")
    r = client.post(f"/api/cases/{closed.id}/transition", headers=lead, json={"to_state": "triaged", "note": "short"})
    assert r.status_code in (409, 422)
    r = client.post(f"/api/cases/{closed.id}/transition", headers=as_user("aisha.rahman"),
                    json={"to_state": "triaged", "note": "new information from the hospital"})
    assert r.status_code == 403


def test_decline_transfer_requires_reason():
    from wasl.services import workflow as wf

    class _C:
        transfer_pending_owner_id = None
    with pytest.raises(Invalid):
        wf.decide_transfer(None, _C(), Actor(user_id=1, role="clinical_lead", display="lead"), False, "")


def test_signoff_registry_documented():
    doc = open("docs/human_signoff.md", encoding="utf-8").read()
    for action in HIGH_IMPACT:
        assert f"`{action}`" in doc
