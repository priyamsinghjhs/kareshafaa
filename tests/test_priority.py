"""Deterministic queue priority, missed-appointment risk bands, and equity reporting."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from kareshafaa.ml.features import EXCLUDED_FROM_INPUTS, FAIRNESS_ATTRS, FEATURES
from kareshafaa.services import priority as P
from kareshafaa.services.queue import sort_key, work_queue


def _row(**kw):
    base = {"id": 1, "tier": "routine", "sla_status": "green", "risk_score": 0.1, "due_at": "2026-11-20T08:00:00",
            "review_required": False, "read_back_needed": False, "phone_valid": True, "whatsapp": True, "email": "a@b.c"}
    base.update(kw)
    return base


def test_bands():
    assert P.risk_band(None) is None
    assert P.risk_band(0.0)["key"] == "low" and P.risk_band(0.24)["key"] == "low"
    assert P.risk_band(0.25)["key"] == "medium" and P.risk_band(0.49)["key"] == "medium"
    assert P.risk_band(0.50)["key"] == "high" and P.risk_band(0.99)["key"] == "high"
    assert P.risk_band(0.5)["label"] == "High"


def test_score_is_deterministic():
    a = P.priority_score(_row(id=3, risk_score=0.4, sla_status="red"))
    b = P.priority_score(_row(id=3, risk_score=0.4, sla_status="red"))
    assert a == b == P.priority_score(_row(id=99, risk_score=0.4, sla_status="red"))  # id is not an input
    assert P.why_first(_row(sla_status="red", due_at="2026-10-01T00:00:00"), datetime(2026, 11, 1)) == P.why_first(
        _row(sla_status="red", due_at="2026-10-01T00:00:00"), datetime(2026, 11, 1))


def test_lower_tier_never_outranks_higher_even_if_overdue_and_high_risk():
    """A red, high-risk, no-contact, review-flagged routine case still sits below a green critical with no risk."""
    routine = _row(id=1, tier="routine", sla_status="red", risk_score=0.99, read_back_needed=False,
                   review_required=True, phone_valid=False, whatsapp=False, email="")
    urgent = _row(id=2, tier="urgent", sla_status="green", risk_score=None, review_required=False)
    high = _row(id=3, tier="high", sla_status="green", risk_score=0.0)
    critical = _row(id=4, tier="critical", sla_status="green", risk_score=0.0)
    rows = [routine, urgent, high, critical]
    rows.sort(key=sort_key)
    assert [r["tier"] for r in rows] == ["critical", "urgent", "high", "routine"]
    assert P.priority_score(routine) < P.priority_score(high) < P.priority_score(urgent) < P.priority_score(critical)
    # the documented gap: non-tier components cannot close a tier step
    assert P.MAX_NON_TIER < (P.TIER_POINTS["high"] - P.TIER_POINTS["routine"])


def test_why_first_names_distinctive_factors():
    r = _row(sla_status="red", risk_score=0.8, due_at="2026-10-01T00:00:00", phone_valid=False, whatsapp=False, email="")
    why = P.why_first(r, datetime(2026, 11, 1))
    assert "Overdue" in why and "day" in why
    # top two distinctive (overdue 300 pts, high risk 48, missing 40) — overdue and high risk
    assert "High missed-appointment risk" in why
    rb = _row(tier="critical", read_back_needed=True, sla_status="green", risk_score=None)
    assert "read-back" in P.why_first(rb).lower()


def test_deadline_sort_is_earliest_due_first():
    early = _row(id=1, tier="routine", due_at="2026-01-01T00:00:00")
    late = _row(id=2, tier="critical", due_at="2026-12-01T00:00:00")
    none = _row(id=3, tier="urgent", due_at=None)
    rows = [late, none, early]
    rows.sort(key=lambda r: sort_key(r, sort="deadline"))
    assert [r["id"] for r in rows] == [1, 2, 3]
    # default priority still puts critical first
    rows.sort(key=sort_key)
    assert rows[0]["tier"] == "critical"


def test_decorate_adds_band_and_why():
    d = P.decorate(_row(risk_score=0.7, sla_status="amber"))
    assert d["risk_band"]["key"] == "high" and d["priority_score"] > 0 and d["why_first"]


def test_protected_attributes_are_not_priority_or_model_inputs():
    for attr in ("language", "nationality_group", "gender", "age_band"):
        assert attr not in FEATURES
        assert attr not in (k for k, _, _ in P.components(_row()))
    assert "gender" in EXCLUDED_FROM_INPUTS and "nationality_group" in EXCLUDED_FROM_INPUTS
    assert "language" in FAIRNESS_ATTRS and "age_band" in FAIRNESS_ATTRS
    # language_unsupported (interpreter need) is an operational barrier, not an identity one-hot
    assert "language_unsupported" in FEATURES


def test_queue_api_returns_band_and_why_and_honours_deadline_sort(client, as_user, db):
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.models import User

    h = as_user("aisha.rahman")
    pri = client.get("/api/queue?view=all&limit=20&sort=priority", headers=h)
    assert pri.status_code == 200
    body = pri.json()
    assert "Priority score" in body["ordering"] and body["sort"] == "priority"
    if body["items"]:
        row = body["items"][0]
        assert "why_first" in row and "priority_score" in row
        assert "risk_band" in row
        # items are in sort_key order
        keys = [sort_key(r) for r in body["items"]]
        assert keys == sorted(keys)
    dead = client.get("/api/queue?view=all&limit=20&sort=deadline", headers=h).json()
    assert dead["sort"] == "deadline" and "Deadline first" in dead["ordering"]
    dues = [r["due_at"] or "9" for r in dead["items"]]
    assert dues == sorted(dues)


def test_score_band_equity_on_analyst_dashboard(client, as_user, db):
    h = as_user("analyst")
    f = client.get("/api/analytics/fairness", headers=h)
    assert f.status_code == 200
    d = f.json()
    bands = d["score_bands"]
    assert "more outreach support" in bands["note"].lower()
    assert "not" in bands["note"].lower() and "input" in bands["note"].lower()
    for rows in (bands["by_language"], bands["by_nationality_group"]):
        assert isinstance(rows, list)
        for r in rows:
            n = r["n"]
            assert isinstance(n, str) or n is None or n >= 5


def test_within_tier_overdue_outranks_high_risk():
    """An overdue routine case stays ahead of an on-time high-risk routine case with flags."""
    overdue = _row(id=1, tier="routine", sla_status="red", risk_score=0.0)
    on_time = _row(id=2, tier="routine", sla_status="green", risk_score=0.99,
                   review_required=True, phone_valid=False, whatsapp=False, email="",
                   read_back_needed=True)
    assert P.priority_score(overdue) > P.priority_score(on_time)
    rows = [on_time, overdue]
    rows.sort(key=sort_key)
    assert [r["id"] for r in rows] == [1, 2]
    amber = _row(id=3, tier="routine", sla_status="amber", risk_score=0.0)
    assert P.priority_score(amber) > P.priority_score(on_time)
