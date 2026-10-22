"""Governed self-improvement: training, promotion gates, human approval, rollback, drift, model cards."""
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from kareshafaa.ml import registry
from kareshafaa.ml.features import EXCLUDED_FROM_INPUTS, FAIRNESS_ATTRS
from kareshafaa.models import Case, ModelVersion
from kareshafaa.services.errors import DomainError, Forbidden


def test_sensitive_attributes_not_model_inputs():
    assert {"gender", "nationality_group"} <= set(EXCLUDED_FROM_INPUTS)
    assert not set(EXCLUDED_FROM_INPUTS) & set(registry.FEATURES)
    assert {"language", "nationality_group", "gender", "age_band", "region"} <= set(FAIRNESS_ATTRS)


def test_metric_helpers():
    y = np.array([0, 0, 1, 1])
    assert registry.fast_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert registry.fast_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert registry.ece(np.array([0, 1] * 50), np.full(100, 0.5)) < 1e-9


def _fair_ok():
    return {"language": {"underserved_auc": [], "underserved_tpr": [], "miscalibrated": [], "auc_gap": 0.01,
                         "tpr_gap": 0.02}}


def test_gates_logic():
    m = {"auc": 0.70, "ece": 0.05}
    assert registry.gate_results(m, _fair_ok(), None)["all_passed"]
    assert not registry.gate_results(m, _fair_ok(), 0.72)["all_passed"]  # must beat current
    assert not registry.gate_results({"auc": 0.55, "ece": 0.05}, _fair_ok(), None)["all_passed"]  # AUC floor
    assert not registry.gate_results({"auc": 0.70, "ece": 0.2}, _fair_ok(), None)["all_passed"]  # calibration
    unfair = _fair_ok()
    unfair["language"]["underserved_tpr"] = ["ur"]
    g = registry.gate_results(m, unfair, None)
    assert not g["all_passed"] and not g["fairness_recall:language"]["passed"]


def test_evaluate_reports_fairness_per_attribute():
    rng = np.random.default_rng(0)
    n = 600
    y = rng.integers(0, 2, n)
    p = np.clip(y * 0.3 + rng.random(n) * 0.7, 0, 1)
    groups = pd.DataFrame({a: rng.choice(["x", "y"], n) for a in FAIRNESS_ATTRS})
    metrics, fair = registry.evaluate(y, p, groups)
    assert set(fair) == set(FAIRNESS_ATTRS)
    assert 0.5 < metrics["auc"] <= 1 and "calibration_curve" in metrics


def test_seed_produced_registry(client, as_user):
    items = client.get("/api/governance/models", headers=as_user("analyst")).json()
    items = items["items"] if isinstance(items, dict) else items
    assert len(items) >= 2
    for m in items:
        assert m["status"] in {"production", "awaiting_approval", "failed_gates", "retired", "rejected", "rolled_back"}


def test_train_requires_permission(client, as_user):
    assert client.post("/api/governance/models/train", headers=as_user("aisha.rahman"),
                       json={"algorithm": "logreg"}).status_code == 403
    assert client.post("/api/governance/models/train", headers=as_user("analyst"),
                       json={"algorithm": "logreg"}).status_code == 403


def test_promotion_lifecycle_and_rollback(client, as_user, db):
    admin, lead, gp = as_user("admin"), as_user("fatima.al.mansoori"), as_user("omar.haddad")
    r = client.post("/api/governance/models/train", headers=admin, json={"algorithm": "logreg"})
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    mv = client.get(f"/api/governance/models/{v}", headers=lead).json()
    assert "gates" in mv and "fairness" in mv and mv["metrics"]["auc"] is not None
    card = client.get(f"/api/governance/models/{v}/card", headers=lead)
    assert card.status_code == 200 and "Intended use" in card.text and "NOT" in card.text
    # admin trained it but cannot approve; GP cannot approve
    assert client.post(f"/api/governance/models/{v}/approve", headers=admin, json={"note": "ship it"}).status_code == 403
    assert client.post(f"/api/governance/models/{v}/approve", headers=gp, json={"note": "ship it"}).status_code == 403

    db.expire_all()
    cand = db.scalar(select(ModelVersion).where(ModelVersion.version == v))
    if cand.status == "failed_gates":
        r = client.post(f"/api/governance/models/{v}/approve", headers=lead, json={"note": "override please"})
        assert r.status_code == 409  # a failed candidate can never be promoted, even by a clinical lead
        # make a passing candidate for the rest of the lifecycle (test-only manipulation of gate outcome)
        cand.status, cand.gates = "awaiting_approval", {**cand.gates, "all_passed": True}
        db.commit()
    prod_before = db.scalar(select(ModelVersion).where(ModelVersion.status == "production"))
    r = client.post(f"/api/governance/models/{v}/approve", headers=lead, json={"note": "Reviewed metrics and fairness."})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalar(select(ModelVersion).where(ModelVersion.status == "production")).version == v
    open_scored = db.scalars(select(Case.risk_model_version).where(Case.state != "closed")).all()
    assert open_scored and all(x == v for x in open_scored)
    # exactly one production model
    assert len(db.scalars(select(ModelVersion).where(ModelVersion.status == "production")).all()) == 1
    if prod_before is None:
        # nothing to roll back to yet -> refused; promote a second version so rollback has a target
        r = client.post("/api/governance/models/rollback", headers=lead, json={"note": "Drift alert on region slice."})
        assert r.status_code == 409
        r = client.post("/api/governance/models/train", headers=admin, json={"algorithm": "gbm"})
        v2 = r.json()["version"]
        db.expire_all()
        c2 = db.scalar(select(ModelVersion).where(ModelVersion.version == v2))
        c2.status, c2.gates = "awaiting_approval", {**c2.gates, "all_passed": True}
        db.commit()
        assert client.post(f"/api/governance/models/{v2}/approve", headers=lead,
                           json={"note": "Reviewed metrics and fairness."}).status_code == 200
        restore, rolled = v, v2
    else:
        restore, rolled = prod_before.version, v
    r = client.post("/api/governance/models/rollback", headers=lead, json={"note": "Drift alert on region slice."})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalar(select(ModelVersion).where(ModelVersion.status == "production")).version == restore
    assert db.scalar(select(ModelVersion).where(ModelVersion.version == rolled)).status == "rolled_back"


def test_service_layer_approval_guards(db, seeded):
    from kareshafaa.security import ML_SERVICE, Actor

    mv = db.scalar(select(ModelVersion).order_by(ModelVersion.version.desc()))
    with pytest.raises(Forbidden):
        registry.approve(db, mv.version, ML_SERVICE, "self-approve")
    analyst = Actor(user_id=None, role="analyst", display="a")
    with pytest.raises(Forbidden):
        registry.approve(db, mv.version, analyst, "approve")
    lead = Actor(user_id=None, role="clinical_lead", display="lead")
    prod = registry.production(db)
    if prod:
        with pytest.raises(DomainError):
            registry.approve(db, prod.version, lead, "already prod")
    db.rollback()


def test_artifact_integrity_check(db, seeded):
    mv = registry.production(db) or db.scalar(select(ModelVersion).limit(1))
    registry.load_artifact(mv)
    good = mv.artifact_sha256
    mv.artifact_sha256 = "0" * 64
    with pytest.raises(DomainError):
        registry.load_artifact(mv)
    mv.artifact_sha256 = good
    db.rollback()


def test_drift_report(client, as_user):
    r = client.get("/api/governance/drift", headers=as_user("fatima.al.mansoori"))
    assert r.status_code == 200
    d = r.json()
    assert "features" in d or "status" in d


def test_risk_model_only_orders_queue(client, as_user):
    """Risk never overrides SLA status: within the queue, RAG status dominates ML risk."""
    items = client.get("/api/queue?view=all&limit=500", headers=as_user("aisha.rahman")).json()["items"]
    rank = {"red": 0, "amber": 1, "green": 2}
    keys = [(rank.get(i["sla_status"], 3)) for i in items]
    assert keys == sorted(keys)
