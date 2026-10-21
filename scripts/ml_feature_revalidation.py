"""Re-check the round-18 language feature choice on FRESH cohorts (seeds never used for that choice).

Round 18 replaced the one-hot `language` model input by `language_unsupported` (interpreter needed) after looking at
the demo/10k/20k hold-outs. This script re-runs that comparison on databases seeded with new seeds, so the result is
not tuned to the data the choice was made on. It is read-only on the source DBs (works on a scratch copy) and never
promotes anything. Gates, tolerances, minimum group sizes and definitions are the production ones, unchanged.

Decision rule (pre-specified in round 19 before any fresh-cohort result was seen):
  CONFIRMED if, on every fresh cohort and for both algorithms (recalibration = production default 'none'),
    (a) the production feature set ("barrier") passes every promotion gate, and
    (b) its hold-out AUC is not credibly worse than either alternative: the paired-bootstrap 95 % CI of
        AUC(barrier) - AUC(alternative) must not lie wholly below -0.01.
  Otherwise NOT CONFIRMED: report it; do not change any gate to make it pass.

Second pre-registered check (round 19, written after the first three fresh cohorts and BEFORE the second set was
seeded): the first check was NOT confirmed (the barrier set failed `fairness_recall:nationality_group` on the fresh
demo and fresh 10k for both algorithms; it passed on the fresh 20k), while the "none" set passed every gate on all
three. Because "none" was picked by looking at those cohorts, it is tested with `--primary none` on three NEW seeds
with the same rule (primary passes every gate for both algorithms on every cohort, and its AUC is not credibly worse
than either alternative). Only if that holds may the production inputs change; otherwise they stay as they are.

usage: python scripts/ml_feature_revalidation.py --db PATH --name NAME [--db PATH --name NAME ...] [--primary barrier]
       [--out docs/ml_feature_revalidation]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE_CAT = ["programme", "tier", "region"]
BASE_NUM = ["has_smartphone", "whatsapp", "phone_valid", "alt_contact", "prior_no_shows", "prior_screens",
            "early_failed_contacts", "early_notified", "early_teach_back_failed"]
FEATURE_SETS = {
    "onehot": (BASE_CAT + ["language"], BASE_NUM),            # pre-round-18 inputs
    "none": (BASE_CAT, BASE_NUM),                              # no language information at all
    "barrier": (BASE_CAT, BASE_NUM + ["language_unsupported"]),  # production since round 18
}


def _use(cat: list[str], num: list[str]) -> None:
    from wasl.ml import calibration, features, registry  # noqa: PLC0415

    for mod in (registry, calibration, features):
        mod.CAT_FEATURES, mod.NUM_FEATURES, mod.FEATURES = list(cat), list(num), list(cat) + list(num)


def _paired_auc_diff(y: np.ndarray, pa: np.ndarray, pb: np.ndarray, reps: int = 1000, seed: int = 20261919):
    from wasl.ml.registry import fast_auc  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    n, d = len(y), []
    for _ in range(reps):
        i = rng.integers(0, n, n)
        a, b = fast_auc(y[i], pa[i]), fast_auc(y[i], pb[i])
        if a is not None and b is not None:
            d.append(a - b)
    point = (fast_auc(y, pa) or 0) - (fast_auc(y, pb) or 0)
    return round(float(point), 4), [round(float(np.quantile(d, 0.025)), 4), round(float(np.quantile(d, 0.975)), 4)]


def run_cohort(db_path: str, primary: str = "barrier") -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="mlfeat_"))
    src, dst = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True), sqlite3.connect(tmp / "w.db")
    src.backup(dst)
    src.close()
    dst.close()
    os.environ["WASL_DATABASE_URL"] = f"sqlite:///{tmp / 'w.db'}"
    os.environ["WASL_MODEL_DIR"] = str(tmp / "models")
    (tmp / "models").mkdir()
    import joblib  # noqa: PLC0415
    from sqlalchemy import func, select  # noqa: PLC0415

    from wasl import config as wcfg  # noqa: PLC0415
    from wasl import db as wdb  # noqa: PLC0415

    for f in (getattr(wcfg.get_settings, "cache_clear", None), getattr(wdb, "reset_engine", None)):
        if f:
            f()
    from wasl.config import get_settings  # noqa: PLC0415
    from wasl.db import session_factory  # noqa: PLC0415
    from wasl.ml import features, registry  # noqa: PLC0415
    from wasl.models import AuditEvent, ModelVersion  # noqa: PLC0415
    from wasl.security import Actor  # noqa: PLC0415

    cfg = get_settings()
    assert str(tmp) in str(cfg.database_url), "refusing to run against a non-scratch database"
    cfg.ml_recalibration = "none"
    db = session_factory()()
    now = db.scalar(select(func.max(AuditEvent.ts)))
    actor = Actor(user_id=None, role="admin", display="feature re-validation (offline)")
    out: dict = {"as_of": str(now), "results": {}, "paired_auc_diff": {}}
    preds: dict = {}
    for alg in ("logreg", "gbm"):
        for fs, (cat, num) in FEATURE_SETS.items():
            _use(cat, num)
            for mv in db.scalars(select(ModelVersion).where(ModelVersion.status == "production")):
                mv.status = "retired"
            mv = registry.train_candidate(db, actor, alg, now=now)
            db.commit()
            df = features.training_frame(db, now)
            va = df.iloc[int(len(df) * 0.75):].reset_index(drop=True)
            p = joblib.load(mv.artifact_path).predict_proba(va)[:, 1]
            preds[(alg, fs)] = (va.label.to_numpy(), p)
            lang = mv.fairness["language"]
            cal = mv.metrics["calibration"]["calibrated"]
            out["results"][f"{alg}/{fs}"] = {
                "n_train": mv.trained_on_n, "n_holdout": mv.validated_on_n, "auc": mv.metrics["auc"],
                "ece": cal["ece"], "slope": cal["slope"], "citl": cal["citl"],
                "bins": f"{cal['bins_within_ci']}/{cal['bins']}", "recall_at_top": mv.metrics["recall_at_top"],
                "all_gates_passed": mv.gates["all_passed"],
                "gates_failed": [k for k, v in mv.gates.items() if k != "all_passed" and not v["passed"]],
                "language_recall_gap": lang["tpr_gap"],
                "language_groups": [{k: g.get(k) for k in ("group", "n", "n_pos", "eligible_for_gate", "tpr_at_top",
                                                          "tpr_ci", "flag_rate", "observed_rate")}
                                    for g in lang["groups"]],
                "failing_attribute_groups": {
                    attr: [{k: g.get(k) for k in ("group", "n", "n_pos", "eligible_for_gate", "tpr_at_top", "tpr_ci",
                                                  "auc", "auc_ci", "flag_rate", "observed_rate", "calibration_diff")}
                           for g in f["groups"]]
                    for attr, f in mv.fairness.items()
                    if f["underserved_tpr"] or f["underserved_auc"] or f["miscalibrated"]}}
        y, pb = preds[(alg, primary)]
        for alt in (x for x in FEATURE_SETS if x != primary):
            y2, pa = preds[(alg, alt)]
            assert np.array_equal(y, y2)
            out["paired_auc_diff"][f"{alg}: {primary} - {alt}"] = _paired_auc_diff(y, pb, pa)
    db.close()
    _use(*FEATURE_SETS["barrier"])
    shutil.rmtree(tmp, ignore_errors=True)
    ok_gates = all(out["results"][f"{a}/{primary}"]["all_gates_passed"] for a in ("logreg", "gbm"))
    ok_auc = all(ci[1] >= -0.01 for _, ci in out["paired_auc_diff"].values())
    out["primary"] = primary
    out["confirmed"] = {f"{primary}_passes_all_gates": ok_gates, f"{primary}_auc_not_credibly_worse": ok_auc,
                        "confirmed": ok_gates and ok_auc}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="append", required=True)
    ap.add_argument("--name", action="append", required=True)
    ap.add_argument("--out", default=str(ROOT / "docs" / "ml_feature_revalidation"))
    ap.add_argument("--primary", default="barrier", choices=sorted(FEATURE_SETS))
    a = ap.parse_args()
    assert len(a.db) == len(a.name)
    jpath = Path(a.out).with_suffix(".json")
    data = json.loads(jpath.read_text()) if jpath.exists() else {}
    for path, name in zip(a.db, a.name):
        data[name] = {"db": path} | run_cohort(path, a.primary)
        jpath.write_text(json.dumps(data, indent=1, default=str))
        d = data[name]
        for k, r in d["results"].items():
            print(f"[{name}] {k:16s} AUC={r['auc']} ECE={r['ece']} slope={r['slope']} CITL={r['citl']} bins={r['bins']} "
                  f"lang_gap={r['language_recall_gap']} failed={r['gates_failed']}")
        for k, v in d["paired_auc_diff"].items():
            print(f"[{name}] dAUC {k}: {v[0]} CI {v[1]}")
        print(f"[{name}] {d['confirmed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
