"""Round 20: retrain the outreach-priority model on POOLED cohorts incl. the edge-case cohort; report hold-out
discrimination, calibration and the fairness gates PER COHORT. Offline and read-only: nothing is registered or
promoted (promotion stays a clinical-lead sign-off in the registry). Feature set = wasl.ml.features.FEATURES
(round-18 language-barrier input unchanged).

Each cohort is split temporally (oldest 75 % train, most recent 25 % hold-out); the pooled model trains on the
union of the train parts and is evaluated on each cohort's own hold-out, next to a model trained on that cohort
alone (same split) so the effect of adding the edge cohort is visible.

usage: python scripts/ml_pooled_retrain.py --db demo=data/wasl.db --db realistic_20k=data/wasl_large.db \
          --db edge_20k=data/wasl_edge.db [--out docs/ml_pooled_retrain]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wasl.config import get_settings  # noqa: E402
from wasl.ml.calibration import CalibratedModel, calibration_stats  # noqa: E402
from wasl.ml.features import FAIRNESS_ATTRS, FEATURES, training_frame  # noqa: E402
from wasl.ml.registry import _pipeline, evaluate, gate_results  # noqa: E402
from wasl.models import AuditEvent  # noqa: E402


def frame(path: str) -> pd.DataFrame:
    eng = create_engine(f"sqlite:///{path}")
    with Session(eng) as db:
        e = db.scalar(select(AuditEvent).where(AuditEvent.event == "seed.finished").order_by(AuditEvent.id.desc()))
        now = e.ts if e else None
        return training_frame(db, now)


def fit_eval(tr: pd.DataFrame, va: pd.DataFrame, algo: str) -> dict:
    cfg = get_settings()
    m = CalibratedModel(_pipeline(algo, cfg.seed), method=cfg.ml_recalibration).fit(tr[FEATURES], tr.label.to_numpy())
    y = va.label.to_numpy()
    p = m.predict_proba(va[FEATURES])[:, 1]
    metrics, fair = evaluate(y, p, va[FAIRNESS_ATTRS])
    metrics["calibration"] = {"calibrated": calibration_stats(y, p)}
    g = gate_results(metrics, fair, None)
    cal = metrics["calibration"]["calibrated"]
    return {"auc": metrics.get("auc"), "ece": metrics.get("ece"), "slope": cal.get("slope"), "citl": cal.get("citl"),
            "brier": cal.get("brier"), "n_train": len(tr), "n_holdout": len(va), "positive_rate": round(float(y.mean()), 3),
            "gates_failed": [k for k, v in g.items() if isinstance(v, dict) and not v.get("passed", True)]
            or ([] if g.get("all_passed") else ["see gates"]), "all_gates_passed": bool(g.get("all_passed"))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="append", required=True, help="name=path")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    parts = {}
    for spec in a.db:
        name, path = spec.split("=", 1)
        df = frame(path).reset_index(drop=True)
        cut = int(len(df) * 0.75)
        parts[name] = (df.iloc[:cut], df.iloc[cut:].reset_index(drop=True))
        print(f"{name}: {len(df):,} labelled cases (positive rate {df.label.mean():.3f})", flush=True)
    pooled_tr = pd.concat([t for t, _ in parts.values()], ignore_index=True)
    no_edge_tr = pd.concat([t for n, (t, _) in parts.items() if not n.startswith("edge")], ignore_index=True)
    # The edge cohort is STRATIFIED (about 40 % positives by design), so naive pooling shifts the base rate the model
    # learns. Prior-matched variant: keep every edge negative and a seeded random subset of edge positives so the edge
    # rows carry the realistic cohorts' positive rate (edge scenarios kept, prevalence not imported).
    target = float(no_edge_tr.label.mean())
    pm = []
    for n, (t, _) in parts.items():
        if n.startswith("edge"):
            neg, pos = t[t.label == 0], t[t.label == 1]
            k = int(round(len(neg) * target / (1 - target)))
            pm.append(pd.concat([neg, pos.sample(n=min(k, len(pos)), random_state=2020)]))
        else:
            pm.append(t)
    prior_tr = pd.concat(pm, ignore_index=True)
    out: dict = {"features": FEATURES, "cohorts": {}, "prior_matched_target_rate": round(target, 4)}
    for algo in ("logreg", "gbm"):
        for name, (tr, va) in parts.items():
            r = out["cohorts"].setdefault(name, {})
            r[f"{algo}:own_cohort"] = fit_eval(tr, va, algo)
            r[f"{algo}:pooled_without_edge"] = fit_eval(no_edge_tr, va, algo)
            r[f"{algo}:pooled_with_edge"] = fit_eval(pooled_tr, va, algo)
            r[f"{algo}:pooled_with_edge_prior_matched"] = fit_eval(prior_tr, va, algo)
            print(name, algo, {k: (v["auc"], v["ece"], v["all_gates_passed"]) for k, v in r.items() if k.startswith(algo)},
                  flush=True)
    if a.out:
        Path(a.out + ".json").write_text(json.dumps(out, indent=1, default=float))
        L = ["# ML outreach-priority model: pooled retrain incl. the edge-case cohort (round 20)", "",
             "Generated by `scripts/ml_pooled_retrain.py` (offline, read-only; **nothing registered or promoted** - "
             "promotion remains a clinical-lead sign-off). Inputs unchanged (round-18 language-barrier input, pending "
             f"human sign-off): `{', '.join(FEATURES)}`. Temporal split per cohort (oldest 75 % train / newest 25 % "
             "hold-out). The model only orders outreach within a tier; it never sets or changes a clinical tier.", "",
             "| Cohort | Model | Trained on | n train / hold-out | Hold-out positive rate | AUC | ECE | Slope | CITL | Brier "
             "| Fairness / promotion gates |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, r in out["cohorts"].items():
            for k, v in r.items():
                algo, how = k.split(":")
                L.append(f"| {name} | {algo} | {how.replace('_', ' ')} | {v['n_train']:,} / {v['n_holdout']:,} | "
                         f"{v['positive_rate']} | {v['auc']} | {v['ece']} | {v['slope']} | {v['citl']} | {v['brier']} | "
                         f"{'all pass' if v['all_gates_passed'] else 'FAIL: ' + ', '.join(v['gates_failed'])} |")
        Path(a.out + ".md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
