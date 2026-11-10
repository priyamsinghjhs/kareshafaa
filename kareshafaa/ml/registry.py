"""Model registry: train candidates, evaluate gates (performance, calibration, fairness),
human approval, rollback, drift monitoring, model cards. Every action is audited."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..models import Case, ModelArtifact, ModelVersion, utcnow
from ..security import ML_SERVICE, Actor
from ..signoff import require_signoff
from ..services.errors import DomainError, Forbidden, Invalid, NotFound
from .calibration import CalibratedModel, calibration_stats, explain, reference_profile
from .features import (CAT_FEATURES, EXCLUDED_FROM_INPUTS, FAIRNESS_ATTRS, FEATURES, LABEL_DEFINITION, NUM_FEATURES,
                       case_frame, training_frame)

ALGORITHMS = ("logreg", "gbm")


def _pipeline(algorithm: str, seed: int) -> Pipeline:
    pre = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES),
                             ("num", StandardScaler(), NUM_FEATURES)])
    if algorithm == "logreg":
        clf: Any = LogisticRegression(max_iter=2000, C=1.0)
    elif algorithm == "gbm":
        clf = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.05, subsample=0.9,
                                         random_state=seed)
    else:
        raise Invalid(f"algorithm must be one of {ALGORITHMS}")
    return Pipeline([("pre", pre), ("clf", clf)])


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    e = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            e += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(e)


def calibration_curve(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.sum() >= 5:
            out.append({"bin": b, "mean_pred": round(float(p[m].mean()), 3), "observed": round(float(y[m].mean()), 3),
                        "n": int(m.sum())})
    return out


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    return fast_auc(y, p)


def fast_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Rank-based (Mann-Whitney) AUC; None if only one class is present."""
    pos = y == 1
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return None
    ranks = pd.Series(p).rank().to_numpy()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _boot_ci(fn, y: np.ndarray, p: np.ndarray, f: np.ndarray, rng: np.random.Generator, b: int = 200) -> tuple[float, float] | None:
    vals = []
    n = len(y)
    for _ in range(b):
        ii = rng.integers(0, n, n)
        v = fn(y[ii], p[ii], f[ii])
        if v is not None:
            vals.append(v)
    if len(vals) < b // 2:
        return None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _tpr(y, p, f):
    return float(f[y == 1].mean()) if (y == 1).any() else None


def _auc(y, p, f):
    return fast_auc(y, p)


def _cal(y, p, f):
    return float(p.mean() - y.mean())


def evaluate(y: np.ndarray, p: np.ndarray, groups: pd.DataFrame) -> tuple[dict, dict]:
    """Held-out metrics plus per-group fairness metrics with bootstrap 95% CIs.

    Fairness gates (see gate_results) fail when there is statistically credible evidence that an
    eligible group is under-served: its AUC or recall-at-top CI lies entirely below the overall
    value minus a tolerance, or its calibration-in-the-large CI lies entirely outside +/- tolerance.
    """
    cfg = get_settings()
    rng = np.random.default_rng(cfg.seed)
    thr = float(np.quantile(p, 1 - cfg.priority_top_fraction))
    flag = p >= thr
    pos = y == 1
    overall_auc = fast_auc(y, p) or 0.0
    overall_tpr = float(flag[pos].mean()) if pos.any() else 0.0
    metrics = {
        "auc": round(overall_auc, 4), "brier": round(float(brier_score_loss(y, p)), 4),
        "ece": round(ece(y, p), 4), "prevalence": round(float(y.mean()), 4), "n": int(len(y)),
        "priority_threshold": round(thr, 4), "top_fraction": cfg.priority_top_fraction,
        "recall_at_top": round(overall_tpr, 4),
        "precision_at_top": round(float(y[flag].mean()) if flag.any() else 0.0, 4),
        "calibration_curve": calibration_curve(y, p),
    }
    fairness: dict[str, Any] = {}
    for attr in FAIRNESS_ATTRS:
        rows = []
        for g, idx in groups.groupby(attr).groups.items():
            ii = groups.index.get_indexer(idx)
            yy, pp, ff = y[ii], p[ii], flag[ii]
            n, npos = len(ii), int((yy == 1).sum())
            eligible = n >= cfg.fairness_min_group_n and npos >= cfg.fairness_min_group_pos and npos < n
            row = {"group": str(g), "n": n, "n_pos": npos, "eligible_for_gate": bool(eligible),
                   "auc": round(fast_auc(yy, pp), 4) if eligible else None,
                   "tpr_at_top": round(_tpr(yy, pp, ff), 4) if npos >= 5 else None,
                   "flag_rate": round(float(ff.mean()), 4), "observed_rate": round(float(yy.mean()), 4),
                   "mean_pred": round(float(pp.mean()), 4), "calibration_diff": round(_cal(yy, pp, ff), 4)}
            if eligible:
                for key, fn in (("auc_ci", _auc), ("tpr_ci", _tpr), ("cal_ci", _cal)):
                    ci = _boot_ci(fn, yy, pp, ff, rng)
                    row[key] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
            rows.append(row)
        el = [r for r in rows if r["eligible_for_gate"]]
        aucs = [r["auc"] for r in el if r["auc"] is not None]
        tprs = [r["tpr_at_top"] for r in el if r["tpr_at_top"] is not None]
        under_auc = [r["group"] for r in el if r.get("auc_ci") and r["auc_ci"][1] < overall_auc - cfg.fairness_auc_tolerance]
        under_tpr = [r["group"] for r in el if r.get("tpr_ci") and r["tpr_ci"][1] < overall_tpr - cfg.fairness_tpr_tolerance]
        miscal = [r["group"] for r in el if r.get("cal_ci") and (r["cal_ci"][0] > cfg.fairness_cal_tolerance
                                                                 or r["cal_ci"][1] < -cfg.fairness_cal_tolerance)]
        fairness[attr] = {"groups": rows,
                          "auc_gap": round(max(aucs) - min(aucs), 4) if len(aucs) >= 2 else 0.0,
                          "tpr_gap": round(max(tprs) - min(tprs), 4) if len(tprs) >= 2 else 0.0,
                          "underserved_auc": under_auc, "underserved_tpr": under_tpr, "miscalibrated": miscal,
                          "eligible_groups": len(el)}
    return metrics, fairness


def gate_results(metrics: dict, fairness: dict, current_auc_on_same_holdout: float | None) -> dict:
    cfg = get_settings()
    gates: dict[str, Any] = {
        "auc_floor": {"value": metrics["auc"], "threshold": cfg.min_auc_floor, "passed": metrics["auc"] >= cfg.min_auc_floor},
        "calibration_ece": {"value": metrics["ece"], "threshold": cfg.max_ece, "passed": metrics["ece"] <= cfg.max_ece},
    }
    cal = metrics.get("calibration", {}).get("calibrated")
    if cal and cal.get("slope") is not None:
        lo, hi = cfg.calibration_slope_range
        gates["calibration_slope"] = {"value": cal["slope"], "threshold": f"{lo}-{hi}", "passed": lo <= cal["slope"] <= hi}
        gates["calibration_in_the_large"] = {"value": cal["citl"], "threshold": f"+/-{cfg.max_citl}",
                                             "passed": abs(cal["citl"]) <= cfg.max_citl}
    if current_auc_on_same_holdout is None:
        gates["beats_current"] = {"value": metrics["auc"], "threshold": None, "passed": True,
                                  "note": "no production model yet"}
    else:
        need = current_auc_on_same_holdout + cfg.promotion_min_auc_delta
        gates["beats_current"] = {"value": metrics["auc"], "threshold": round(need, 4), "passed": metrics["auc"] > need,
                                  "note": "current production model re-scored on the same held-out set"}
    for attr, f in fairness.items():
        gates[f"fairness_auc:{attr}"] = {"value": f["underserved_auc"] or "none", "threshold":
                                         f"no group AUC CI wholly below overall-{cfg.fairness_auc_tolerance}",
                                         "passed": not f["underserved_auc"], "gap": f["auc_gap"]}
        gates[f"fairness_recall:{attr}"] = {"value": f["underserved_tpr"] or "none", "threshold":
                                            f"no group recall CI wholly below overall-{cfg.fairness_tpr_tolerance}",
                                            "passed": not f["underserved_tpr"], "gap": f["tpr_gap"]}
        gates[f"fairness_calibration:{attr}"] = {"value": f["miscalibrated"] or "none", "threshold":
                                                 f"no group calibration CI wholly outside +/-{cfg.fairness_cal_tolerance}",
                                                 "passed": not f["miscalibrated"]}
    gates["all_passed"] = all(g["passed"] for g in gates.values())
    return gates


def _reference_stats(df: pd.DataFrame, scores: np.ndarray) -> dict:
    ref: dict[str, Any] = {"categorical": {}, "score_hist": np.histogram(scores, bins=10, range=(0, 1))[0].tolist()}
    for c in CAT_FEATURES + ["language", "has_smartphone", "whatsapp", "phone_valid", "alt_contact", "early_notified",
                             "early_teach_back_failed", "language_unsupported"]:
        ref["categorical"][c] = df[c].astype(str).value_counts(normalize=True).round(5).to_dict()
    ref["numeric_means"] = {c: round(float(df[c].mean()), 4) for c in ("prior_no_shows", "prior_screens")}
    return ref


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _restore_artifact(mv: ModelVersion, path: Path) -> Path | None:
    """Missing/changed file (e.g. a fresh container): restore it from the database copy with the SAME SHA-256."""
    from sqlalchemy.orm import object_session

    db = object_session(mv)
    if db is None:
        return None
    art = db.get(ModelArtifact, mv.artifact_sha256)
    if art is None or hashlib.sha256(art.data).hexdigest() != mv.artifact_sha256:
        return None
    for target in (path, get_settings().model_dir / art.filename):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(art.data)
            tmp.replace(target)
            return target
        except OSError:
            continue
    return None


def store_artifact(db: Session, mv: ModelVersion) -> bool:
    """Keep a database copy of a model file whose SHA-256 matches its registry entry (idempotent)."""
    if db.get(ModelArtifact, mv.artifact_sha256) is not None:
        return False
    path = Path(mv.artifact_path)
    if not path.exists() or _sha256(path) != mv.artifact_sha256:
        return False
    db.add(ModelArtifact(sha256=mv.artifact_sha256, version=mv.version, filename=path.name, data=path.read_bytes()))
    db.flush()
    return True


def load_artifact(mv: ModelVersion) -> Pipeline:
    path = Path(mv.artifact_path)
    if not path.exists() or _sha256(path) != mv.artifact_sha256:
        restored = _restore_artifact(mv, path)
        if restored is None or _sha256(restored) != mv.artifact_sha256:
            raise DomainError(f"model artifact for v{mv.version} is missing or fails its integrity check")
        path = restored
    return joblib.load(path)


def production(db: Session) -> ModelVersion | None:
    return db.scalar(select(ModelVersion).where(ModelVersion.status == "production"))


def train_candidate(db: Session, actor: Actor, algorithm: str = "gbm", now: datetime | None = None) -> ModelVersion:
    if not actor.can("model:train"):
        raise Forbidden("role lacks model:train")
    cfg = get_settings()
    now = now or utcnow()
    df = training_frame(db, now)
    if len(df) < 200 or df.label.nunique() < 2:
        raise DomainError("not enough labelled case outcomes to train (need >= 200 with both outcomes)")
    # temporal split: train (and cross-fit the probability calibrator) on the older 75 %, validate on the most
    # recent 25 %, which neither the model nor the calibrator has seen
    cut = int(len(df) * 0.75)
    tr, va = df.iloc[:cut], df.iloc[cut:].reset_index(drop=True)
    pipe = _pipeline(algorithm, cfg.seed)
    model = CalibratedModel(pipe, method=cfg.ml_recalibration).fit(tr[FEATURES], tr.label.to_numpy())
    method = model.method
    y = va.label.to_numpy()
    raw = model.raw_proba(va[FEATURES])
    p = model.predict_proba(va[FEATURES])[:, 1]
    metrics, fairness = evaluate(y, p, va[FAIRNESS_ATTRS])
    metrics["calibration"] = {"method": method, "n_calibration": len(tr), "tuning": model.tuning,
                              "raw": calibration_stats(y, raw),
                              "calibrated": calibration_stats(y, p)}
    cur = production(db)
    cur_auc = None
    if cur is not None:
        try:
            cur_auc = _safe_auc(y, load_artifact(cur).predict_proba(va)[:, 1])  # model picks its own inputs
            metrics["current_production_auc_same_holdout"] = round(cur_auc or 0.0, 4)
        except DomainError:
            cur_auc = None
    gates = gate_results(metrics, fairness, cur_auc)
    version = (db.scalar(select(func.max(ModelVersion.version))) or 0) + 1
    path = cfg.model_dir / f"outreach_priority_v{version}.joblib"
    joblib.dump(model, path)
    mv = ModelVersion(version=version, algorithm=algorithm,
                      status="awaiting_approval" if gates["all_passed"] else "failed_gates", created_at=now,
                      trained_on_n=len(tr), validated_on_n=len(va), features=FEATURES, metrics=metrics,
                      fairness=fairness, gates=gates,
                      reference_stats=_reference_stats(tr, model.predict_proba(tr[FEATURES])[:, 1]) |
                      {"profile": reference_profile(tr)},
                      artifact_path=str(path), artifact_sha256=_sha256(path),
                      compared_to_version=cur.version if cur else None)
    db.add(mv)
    db.flush()
    store_artifact(db, mv)
    mv.model_card = model_card(mv)
    audit.record(db, actor, "C", "model.trained", entity_type="ModelVersion", entity_id=mv.version, ts=now,
                 detail={"algorithm": algorithm, "auc": metrics["auc"], "ece": metrics["ece"],
                         "status": mv.status, "n_train": len(tr), "n_valid": len(va)})
    return mv


def get_version(db: Session, version: int) -> ModelVersion:
    mv = db.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if mv is None:
        raise NotFound(f"model v{version} not found")
    return mv


def approve(db: Session, version: int, actor: Actor, note: str, now: datetime | None = None) -> ModelVersion:
    """Promotion requires: gates passed (incl. beating current + fairness) AND a clinical lead's approval."""
    now = now or utcnow()
    if not actor.can("model:approve") or not actor.is_human:
        raise Forbidden("only a clinical lead can approve a model for production")
    require_signoff(actor, "model.approve", note)
    mv = get_version(db, version)
    if mv.status != "awaiting_approval":
        raise DomainError(f"v{version} is '{mv.status}': only candidates that passed all gates can be promoted")
    if not mv.gates.get("all_passed"):
        raise DomainError("candidate did not pass all promotion gates")
    if not note or len(note.strip()) < 5:
        raise Invalid("an approval note is required")
    cur = production(db)
    if cur is not None:
        cur.status = "retired"
    mv.status = "production"
    mv.approved_by_user_id = actor.user_id
    mv.approved_at = now
    mv.decision_note = note
    mv.model_card = model_card(mv)
    db.flush()
    audit.record(db, actor, "U", "model.approved", entity_type="ModelVersion", entity_id=mv.version, ts=now,
                 detail={"replaced": cur.version if cur else None, "note": note})
    score_open_cases(db, now=now)
    return mv


def reject(db: Session, version: int, actor: Actor, note: str, now: datetime | None = None) -> ModelVersion:
    if not actor.can("model:approve"):
        raise Forbidden("only a clinical lead can reject a candidate")
    require_signoff(actor, "model.reject", note)
    mv = get_version(db, version)
    if mv.status not in ("awaiting_approval", "failed_gates"):
        raise DomainError("only candidates can be rejected")
    mv.status = "rejected"
    mv.decision_note = note
    audit.record(db, actor, "U", "model.rejected", entity_type="ModelVersion", entity_id=mv.version,
                 ts=now or utcnow(), detail={"note": note})
    return mv


def rollback(db: Session, actor: Actor, reason: str, now: datetime | None = None) -> ModelVersion:
    now = now or utcnow()
    if not actor.can("model:rollback"):
        raise Forbidden("only a clinical lead can roll back the production model")
    require_signoff(actor, "model.rollback", reason)
    if not reason or len(reason.strip()) < 5:
        raise Invalid("a rollback reason is required")
    cur = production(db)
    if cur is None:
        raise DomainError("no production model to roll back")
    prev = db.scalar(select(ModelVersion).where(ModelVersion.status == "retired", ModelVersion.approved_at.is_not(None))
                     .order_by(ModelVersion.approved_at.desc()).limit(1))
    if prev is None:
        raise DomainError("no previously approved version to roll back to")
    cur.status = "rolled_back"
    cur.decision_note = (cur.decision_note or "") + f" | rolled back: {reason}"
    prev.status = "production"
    db.flush()
    audit.record(db, actor, "U", "model.rolled_back", entity_type="ModelVersion", entity_id=cur.version, ts=now,
                 detail={"restored": prev.version, "reason": reason})
    score_open_cases(db, now=now)
    return prev


def score_open_cases(db: Session, now: datetime | None = None) -> int:
    """Score open cases with the production model. Writes only Case.risk_score (queue ordering)."""
    from .switch import clear_scores, enabled
    if not enabled(db):  # AI kill switch: no scores at all, the queue uses deadline order
        clear_scores(db)
        return 0
    mv = production(db)
    df = case_frame(db, open_only=True)
    if df.empty:
        return 0
    if mv is None:
        db.query(Case).filter(Case.state != "closed").update({Case.risk_score: None, Case.risk_model_version: None, Case.risk_rationale: None})
        return 0
    model = load_artifact(mv)
    p = model.predict_proba(df)[:, 1]  # the model selects its own input columns (feature sets change)
    profile = (mv.reference_stats or {}).get("profile")
    why = explain(model, df, profile) if profile else [[] for _ in range(len(df))]
    for cid, score, w in zip(df.case_id.tolist(), p.tolist(), why):
        db.query(Case).filter(Case.id == cid).update({Case.risk_score: float(score), Case.risk_model_version: mv.version,
                                                      Case.risk_rationale: w}, synchronize_session=False)
    audit.record(db, ML_SERVICE, "E", "ml.scored_open_cases", entity_type="ModelVersion", entity_id=mv.version,
                 ts=now or utcnow(), detail={"n": len(df), "use": "outreach prioritisation only"})
    return len(df)


def _psi(ref: dict[str, float], cur: dict[str, float]) -> float:
    keys = set(ref) | set(cur)
    eps = 1e-4
    return float(sum((cur.get(k, 0) + eps - ref.get(k, 0) - eps) * np.log((cur.get(k, 0) + eps) / (ref.get(k, 0) + eps))
                     for k in keys))


def drift_report(db: Session) -> dict:
    cfg = get_settings()
    mv = production(db)
    if mv is None:
        return {"status": "no_production_model"}
    df = case_frame(db)
    if not df.empty:
        df = df[df.created_at >= (utcnow() - pd.Timedelta(days=60))]
    if len(df) < 30:
        return {"status": "insufficient_data", "n": len(df)}
    feats = {}
    for c, ref in mv.reference_stats["categorical"].items():
        cur = df[c].astype(str).value_counts(normalize=True).to_dict()
        v = _psi(ref, cur)
        feats[c] = {"psi": round(v, 4), "status": "alert" if v > cfg.drift_psi_alert else "warn" if v > cfg.drift_psi_warn else "ok"}
    scores = load_artifact(mv).predict_proba(df)[:, 1]
    ref_h = np.array(mv.reference_stats["score_hist"], dtype=float)
    cur_h = np.histogram(scores, bins=10, range=(0, 1))[0].astype(float)
    sp = _psi({str(i): v for i, v in enumerate(ref_h / ref_h.sum())}, {str(i): v for i, v in enumerate(cur_h / cur_h.sum())})
    worst = max([f["psi"] for f in feats.values()] + [sp])
    return {"status": "alert" if worst > cfg.drift_psi_alert else "warn" if worst > cfg.drift_psi_warn else "ok",
            "model_version": mv.version, "n_current": len(df), "score_psi": round(sp, 4), "features": feats,
            "thresholds": {"warn": cfg.drift_psi_warn, "alert": cfg.drift_psi_alert},
            "note": "Population: cases created in the last 60 days vs. the training population of the production model."}


def model_card(mv: ModelVersion) -> str:
    m, g = mv.metrics, mv.gates
    lines = [
        f"# Model card - KareShafaa outreach-priority model v{mv.version}",
        "",
        "**TRAINED ON SIMULATED DATA ONLY - not validated for clinical use.**",
        "",
        "## Intended use",
        "Predicts the risk that an actionable screening case will be delayed or drop off, so coordinators can "
        "**prioritise outreach and queue order**.",
        "",
        "## Out-of-scope uses (prohibited and blocked in code)",
        "- Triage, urgency tiering, diagnosis or any clinical decision.",
        "- Moving a case between lifecycle states or closing a case (the state machine never reads the score).",
        "- Deprioritising care: SLA status and urgency tier always sort ahead of the score.",
        "",
        "## Model",
        f"- Algorithm: `{mv.algorithm}` (scikit-learn pipeline: one-hot + scaling + classifier)",
        f"- Created: {mv.created_at:%Y-%m-%d %H:%M} UTC; status: **{mv.status}**",
        f"- Inputs: {', '.join(mv.features)}",
        f"- Excluded protected attributes (used only for fairness evaluation): {', '.join(EXCLUDED_FROM_INPUTS)}",
        f"- Label: {LABEL_DEFINITION}",
        f"- Training cases: {mv.trained_on_n}; temporal hold-out validation cases: {mv.validated_on_n}",
        f"- Artifact SHA-256: `{mv.artifact_sha256}`",
        "",
        "## Validation (held-out, most recent 25% of cases; recalibration chosen on a temporal tuning slice of the training cases)",
        f"- AUC {m['auc']}, Brier {m['brier']}, expected calibration error {m['ece']}, prevalence {m['prevalence']}",
        *([f"- Calibration ({m['calibration']['method']}, chosen on a temporal tuning slice of the {m['calibration']['n_calibration']} training cases): "
           f"ECE {m['calibration']['raw']['ece']} -> {m['calibration']['calibrated']['ece']}, slope "
           f"{m['calibration']['raw']['slope']} -> {m['calibration']['calibrated']['slope']}, calibration-in-the-large "
           f"{m['calibration']['raw']['citl']} -> {m['calibration']['calibrated']['citl']}; reliability bins within "
           f"95 % CI: {m['calibration']['calibrated']['bins_within_ci']}/{m['calibration']['calibrated']['bins']}"]
          if "calibration" in m else []),
        "- Each score is shown with its top factors (one-at-a-time substitution against the training reference "
        "profile) so coordinators can see why a case is prioritised for outreach.",
        f"- Top {int(m['top_fraction'] * 100)}% flagged: recall {m['recall_at_top']}, precision {m['precision_at_top']}",
    ]
    if "current_production_auc_same_holdout" in m:
        lines.append(f"- Current production model on the same hold-out: AUC {m['current_production_auc_same_holdout']}")
    lines += ["", "## Fairness (hold-out; groups below the minimum size are reported but not gated)", "",
              "| Attribute | AUC gap | Recall gap | Under-served (AUC / recall) | Mis-calibrated | Groups |",
              "|---|---|---|---|---|---|"]
    for attr, f in mv.fairness.items():
        grp = "; ".join(f"{r['group']} (n={r['n'] if r['n'] >= 5 else '<5'}, AUC={r['auc']}, recall={r['tpr_at_top']})"
                        for r in f["groups"])
        lines.append(f"| {attr} | {f['auc_gap']} | {f['tpr_gap']} | {', '.join(f['underserved_auc']) or '-'} / "
                     f"{', '.join(f['underserved_tpr']) or '-'} | {', '.join(f['miscalibrated']) or '-'} | {grp} |")
    lines += ["", "## Promotion gates", ""]
    for k, v in g.items():
        if k != "all_passed":
            lines.append(f"- {'PASS' if v['passed'] else 'FAIL'} `{k}`: {v['value']} (rule: {v['threshold']})")
    lines += ["", f"All gates passed: **{g.get('all_passed')}**. Promotion additionally requires approval by a clinical lead.",
              "", "## Governance", f"- Approved by user id: {mv.approved_by_user_id or '-'} at "
              f"{mv.approved_at.isoformat() if mv.approved_at else '-'}; note: {mv.decision_note or '-'}",
              "- Drift monitored (PSI per feature and on the score distribution); rollback available to clinical leads.",
              "- All training, approval, rollback and scoring events are written to the append-only audit trail.",
              "", "## Limitations",
              "- Trained on simulated data with assumed delay/drop-off mechanisms; real performance is unknown.",
              "- Small subgroups have wide uncertainty; fairness gaps are point estimates without confidence intervals.",
              "- Region and the interpreter need (no patient material in the preferred language) are inputs (operational "
              "barriers such as channel reach); language itself and age band are monitored for fairness but are not "
              "inputs. Their effect must be reviewed so higher risk leads to MORE support, never less.",
              "- Aligned with (not certified against) WHO guidance on ethics & governance of AI for health, NIST AI RMF "
              "and ISO/IEC 42001 practices."]
    return "\n".join(lines)
