"""Baseline-vs-loop synthetic arms: intervals, risk difference, equity gap per arm, and the plain statement that
the differences come from the generator's assumptions; shown in the impact report apart from the live figures."""
from __future__ import annotations

from kareshafaa.synth.simulator import ARMS_STATEMENT, run_simulation

LEAD = "fatima.al.mansoori"


def test_arms_carry_intervals_difference_and_statement():
    r = run_simulation(n=300, seed=7, replications=1)
    a = r["arms"]
    assert "generator" in a["statement"] and "not evidence" in a["statement"]
    assert a["measures"]
    for m in a["measures"]:
        for arm in ("baseline", "loop"):
            x = m[arm]
            assert 0 <= x["ci_low"] <= x["rate"] <= x["ci_high"] <= 1 and x["num"] <= m["den"]
        lo, hi = m["difference_ci_pp"]
        assert lo <= m["difference_pp"] <= hi
    assert set(a["equity_gap_care_reached_by_language"]) == {"baseline", "loop"}


def test_impact_report_shows_arms_labelled_synthetic(client, as_user):
    h = as_user(LEAD)
    from kareshafaa.services import analytics
    analytics.clear_cache()
    d = client.get("/api/analytics/impact", headers=h).json()
    sa = d.get("synthetic_arms")
    assert sa is not None, "the seeded database stores a simulation run"
    assert sa["statement"] == ARMS_STATEMENT and sa["measures"]
    csv_text = client.get("/api/analytics/impact?format=csv", headers=h).text
    assert "# synthetic arms" in csv_text and "synthetic_arms,baseline" in csv_text
