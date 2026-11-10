"""Equity figures: numerator, denominator and Wilson 95% interval; no rate for a suppressed cell; unfinished cases
count as not met; best-to-worst gap; conditional median labelled."""
from __future__ import annotations

import pytest

from kareshafaa.services.analytics import prop, wilson
from kareshafaa.services.impact import best_worst_gap

LEAD = "fatima.al.mansoori"


def test_wilson_matches_reference_values():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    lo, hi = wilson(0, 20)
    assert lo == 0.0 and hi == pytest.approx(0.1611, abs=1e-3)
    assert wilson(0, 0) is None


def test_prop_suppressed_cell_has_no_rate_and_no_interval():
    p = prop(2, 3, thr=5)
    assert p["rate"] == "suppressed" and p["ci_low"] is None and p["ci_high"] is None
    assert p["num"] == "<5" and p["den"] == "<5"
    q = prop(30, 50, thr=5)
    assert q["num"] == 30 and q["den"] == 50 and q["rate"] == 0.6 and q["ci_low"] < 0.6 < q["ci_high"]


def test_best_worst_gap_ignores_suppressed_groups():
    rows = [{"group": "A", "loop_closure_rate": 0.9}, {"group": "B", "loop_closure_rate": 0.6},
            {"group": "C", "loop_closure_rate": "suppressed"}]
    g = best_worst_gap(rows)
    assert g["best_group"] == "A" and g["worst_group"] == "B" and g["gap_pp"] == 30.0
    assert g["groups_compared"] == 2 and g["groups_suppressed"] == 1
    assert best_worst_gap(rows[:1] + rows[2:]) is None


def test_impact_report_carries_intervals_gap_and_definitions(client, as_user):
    d = client.get("/api/analytics/impact", headers=as_user(LEAD)).json()
    h = d["headline"]
    assert h["median_days_to_specialist_is_conditional"] is True
    assert {"unfinished_cases", "within_target_all", "interval", "equity_gap", "median_days_to_specialist"} <= set(d["definitions"])
    if isinstance(h["loop_closure_rate"], float):
        assert h["loop_closure_ci_low"] <= h["loop_closure_rate"] <= h["loop_closure_ci_high"]
        assert h["loop_closed"] <= h["mature_cases"]
    if isinstance(h["within_target_rate_all"], float) and isinstance(h["loop_closure_rate"], float):
        # unfinished cases count as not met, so this can never exceed the share that reached care at all
        assert h["within_target_rate_all"] <= h["loop_closure_rate"] + 1e-9
    for e in d["equity"].values():
        for r in e["rows"]:
            if isinstance(r["loop_closure_rate"], float):
                assert r["ci_low"] <= r["loop_closure_rate"] <= r["ci_high"]
            else:
                assert r["ci_low"] is None and r["ci_high"] is None
        if e["gap"]:
            assert e["gap"]["gap_pp"] >= 0
