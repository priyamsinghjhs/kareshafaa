"""ML calibration statistics, the calibration gates and the per-priority rationale (outreach ordering only)."""
import numpy as np
import pandas as pd
from sqlalchemy import select
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from kareshafaa.ml import registry
from kareshafaa.ml.calibration import CalibratedModel, _label, calibration_stats, explain, reference_profile, wilson
from kareshafaa.ml.features import CAT_FEATURES, FEATURES, NUM_FEATURES
from kareshafaa.models import Case, ModelVersion


def test_wilson_interval():
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi and abs((lo + hi) / 2 - 0.5) < 1e-9
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0


def test_calibration_stats_perfect_vs_overconfident():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.6, 20000)
    y = (rng.random(20000) < p).astype(int)
    good = calibration_stats(y, p)
    assert good["ece"] < 0.02 and abs(good["citl"]) < 0.01 and 0.9 < good["slope"] < 1.1
    assert good["bins_within_ci"] >= 8 and good["bins"] == 10
    # over-confident scores (stretched logits) => slope < 1, larger ECE
    lg = np.log(p / (1 - p))
    over = 1 / (1 + np.exp(-(2.5 * (lg - lg.mean()) + lg.mean())))
    bad = calibration_stats(y, over)
    assert bad["slope"] < 0.6 and bad["ece"] > good["ece"]
    # systematic over-prediction shows up in calibration-in-the-large
    assert calibration_stats(y, np.clip(p + 0.1, 0, 1))["citl"] > 0.09


def _gate(**cal):
    m = {"auc": 0.70, "ece": 0.05, "calibration": {"calibrated": {"slope": 1.0, "citl": 0.0, **cal}}}
    fair = {"language": {"underserved_auc": [], "underserved_tpr": [], "miscalibrated": [], "auc_gap": 0, "tpr_gap": 0}}
    return registry.gate_results(m, fair, None)


def test_calibration_gates():
    assert _gate()["all_passed"]
    g = _gate(slope=1.5)
    assert not g["all_passed"] and not g["calibration_slope"]["passed"]
    g = _gate(slope=0.5)
    assert not g["calibration_slope"]["passed"]
    g = _gate(citl=-0.08)
    assert not g["all_passed"] and not g["calibration_in_the_large"]["passed"]


def _frame(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({c: rng.choice(["a", "b", "c"], n) for c in CAT_FEATURES})
    for c in NUM_FEATURES:
        df[c] = rng.integers(0, 3, n).astype(float)
    df["prior_no_shows"] = rng.integers(0, 5, n).astype(float)
    logit = -2 + 0.8 * df["prior_no_shows"] + (df["region"] == "c") * 0.7
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return df, y.to_numpy()


def _pipe():
    pre = ColumnTransformer([("c", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES), ("n", StandardScaler(), NUM_FEATURES)])
    return Pipeline([("pre", pre), ("m", LogisticRegression(max_iter=1000))])


def test_recalibration_is_monotone_and_never_reorders():
    df, y = _frame()
    for method in ("none", "platt", "auto"):
        m = CalibratedModel(_pipe(), method=method).fit(df[FEATURES], y)
        raw, cal = m.raw_proba(df[FEATURES]), m.predict_proba(df[FEATURES])[:, 1]
        assert m.tuning["chosen"] in {"none", "platt"}
        # Platt is a monotone map: the ranking (and hence the within-tier queue order) is identical
        assert (np.argsort(raw, kind="stable") == np.argsort(cal, kind="stable")).all() or \
            np.corrcoef(np.argsort(np.argsort(raw)), np.argsort(np.argsort(cal)))[0, 1] > 0.9999
        assert ((cal >= 0) & (cal <= 1)).all()


def test_explain_names_true_drivers_in_plain_language():
    df, y = _frame()
    m = CalibratedModel(_pipe(), method="none").fit(df[FEATURES], y)
    prof = reference_profile(df)
    rows = df.copy()
    rows["prior_no_shows"] = 4.0
    why = explain(m, rows.head(20), prof)
    assert all(w and w[0]["feature"] == "prior_no_shows" and w[0]["direction"] == "raises" for w in why)
    assert why[0][0]["text"] == "4 previous missed appointment(s)"
    # the reference case itself has nothing to explain
    ref = pd.DataFrame([{f: prof[f] for f in FEATURES}])
    assert explain(m, ref, prof) == [[]]
    for w in why:
        assert len(w) <= 3 and all(abs(x["effect"]) >= 0.02 for x in w)


def test_labels_are_correctly_oriented():
    assert _label("early_teach_back_failed", 1) == "Did not confirm understanding at teach-back"
    assert _label("early_teach_back_failed", 0) == "No failed teach-back recorded"
    assert _label("phone_valid", 0) == "Phone number not valid"
    assert _label("early_notified", 1) == "Notified in the first 3 days"


def _production(client, as_user, db):
    """The 400-patient test seed is too small for a well-calibrated model, so the calibration-slope gate blocks
    promotion (by design). For the rationale tests a candidate is promoted through the real human sign-off API
    after a test-only gate override (same pattern as test_ml_governance)."""
    mv = db.scalar(select(ModelVersion).where(ModelVersion.status == "production"))
    if mv is not None:
        return mv
    cand = db.scalar(select(ModelVersion).where(ModelVersion.algorithm == "logreg").order_by(ModelVersion.version))
    assert cand.gates["calibration_slope"]["value"] is not None and "calibration" in cand.metrics
    cand.status, cand.gates = "awaiting_approval", {**cand.gates, "all_passed": True}
    db.commit()
    r = client.post(f"/api/governance/models/{cand.version}/approve", headers=as_user("fatima.al.mansoori"),
                    json={"note": "Reviewed calibration and fairness (test)."})
    assert r.status_code == 200, r.text
    db.expire_all()
    return db.scalar(select(ModelVersion).where(ModelVersion.status == "production"))


def test_small_sample_overconfident_model_is_blocked(db):
    """Calibration gate is meaningful: an over-confident small-sample model cannot reach production on its own."""
    for mv in db.scalars(select(ModelVersion).where(ModelVersion.version <= 2)):
        g = mv.gates
        if "calibration_slope" in g and not g["calibration_slope"]["passed"]:
            assert not g["all_passed"] and mv.status != "production"


def test_rationale_exposed_and_never_changes_tier(client, as_user, db):
    mv = _production(client, as_user, db)
    cal = mv.metrics["calibration"]
    assert cal["method"] in {"none", "platt"} and "calibrated" in cal and cal["calibrated"]["bins"] >= 5
    scored = db.scalars(select(Case).where(Case.risk_score.is_not(None), Case.state != "closed")).all()
    assert scored and all(c.risk_rationale is not None for c in scored)
    tiers = {c.id: c.tier for c in scored}
    registry.score_open_cases(db)
    db.commit()
    assert {c.id: c.tier for c in db.scalars(select(Case).where(Case.id.in_(tiers)))} == tiers
    c = next(c for c in scored if c.risk_rationale)
    d = client.get(f"/api/cases/{c.id}", headers=as_user("aisha.rahman")).json()
    assert d["risk"]["rationale"] and "WITHIN the same clinical urgency tier" in d["risk"]["ordering"]
    for w in d["risk"]["rationale"]:
        assert w["direction"] in {"raises", "lowers"} and w["text"]
        assert w["feature"] in FEATURES  # never a sensitive attribute
    items = client.get("/api/queue?view=all&limit=500", headers=as_user("aisha.rahman")).json()["items"]
    assert any(i.get("risk_rationale") for i in items)


def test_model_card_reports_calibration(db):
    mv = db.scalar(select(ModelVersion).order_by(ModelVersion.version))
    assert "alibrat" in registry.model_card(mv)


def test_model_with_older_feature_set_still_scores_and_explains(monkeypatch):
    """Iteration 7: age_band was removed as a model input. A production model trained WITH it must keep scoring,
    explaining and being compared against new candidates (it crashed with 'columns are missing')."""
    import numpy as np
    import pandas as pd

    import kareshafaa.ml.registry as R
    from kareshafaa.ml.calibration import CalibratedModel, explain
    from kareshafaa.ml.features import CAT_FEATURES, FEATURES, NUM_FEATURES

    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame({c: rng.choice(["a", "b", "c"], n) for c in CAT_FEATURES})
    for c in NUM_FEATURES:
        df[c] = rng.integers(0, 3, n).astype(float)
    df["age_band"] = rng.choice(["40-49", "60-75"], n)
    y = rng.integers(0, 2, n)
    old_cat = CAT_FEATURES + ["age_band"]
    monkeypatch.setattr(R, "CAT_FEATURES", old_cat)
    old = CalibratedModel(R._pipeline("logreg", 0), method="none").fit(df[old_cat + NUM_FEATURES], y)
    monkeypatch.setattr(R, "CAT_FEATURES", CAT_FEATURES)
    assert "age_band" in old.input_columns and "age_band" not in FEATURES
    p = old.predict_proba(df)[:, 1]  # full frame: the model selects its own columns
    assert p.shape == (n,) and np.all((p > 0) & (p < 1))
    profile = {c: (str(df[c].mode()[0]) if c in old_cat else float(df[c].mean())) for c in old.input_columns}
    why = explain(old, df.head(5), profile)
    assert len(why) == 5
