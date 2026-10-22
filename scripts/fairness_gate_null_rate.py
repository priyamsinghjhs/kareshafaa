"""How often do the fairness gates fail when there is NO group difference? (measurement only; nothing is changed)

Trains the production-feature candidate on a scratch copy of a seeded DB, keeps its hold-out predictions, then
randomly permutes the fairness-attribute rows of the hold-out (all attributes jointly, so their mutual structure is
kept but any link to outcome or score is broken) and re-runs the unchanged production `evaluate` + gates. The share
of permutations in which a fairness gate fails estimates the gates' false-alarm rate at this sample size. Gates,
tolerances and minimum group sizes are NOT changed; the result is for the clinical lead / model governance to read.

usage: python scripts/fairness_gate_null_rate.py --db PATH [--alg logreg] [--reps 200]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--alg", default="logreg")
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261919)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="fairnull_"))
    src, dst = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True), sqlite3.connect(tmp / "w.db")
    src.backup(dst)
    src.close()
    dst.close()
    os.environ["KARESHAFAA_DATABASE_URL"] = f"sqlite:///{tmp / 'w.db'}"
    os.environ["KARESHAFAA_MODEL_DIR"] = str(tmp / "models")
    (tmp / "models").mkdir()
    import joblib  # noqa: PLC0415
    from sqlalchemy import func, select  # noqa: PLC0415

    from kareshafaa.config import get_settings  # noqa: PLC0415
    from kareshafaa.db import session_factory  # noqa: PLC0415
    from kareshafaa.ml import features, registry  # noqa: PLC0415
    from kareshafaa.models import AuditEvent, ModelVersion  # noqa: PLC0415
    from kareshafaa.security import Actor  # noqa: PLC0415

    cfg = get_settings()
    assert str(tmp) in str(cfg.database_url)
    cfg.ml_recalibration = "none"
    db = session_factory()()
    now = db.scalar(select(func.max(AuditEvent.ts)))
    for mv in db.scalars(select(ModelVersion).where(ModelVersion.status == "production")):
        mv.status = "retired"
    mv = registry.train_candidate(db, Actor(user_id=None, role="admin", display="null-rate (offline)"), a.alg, now=now)
    df = features.training_frame(db, now)
    va = df.iloc[int(len(df) * 0.75):].reset_index(drop=True)
    y = va.label.to_numpy()
    p = joblib.load(mv.artifact_path).predict_proba(va)[:, 1]
    groups = va[features.FAIRNESS_ATTRS].reset_index(drop=True)
    observed = {k: v["passed"] for k, v in mv.gates.items() if k.startswith("fairness_")}
    rng = np.random.default_rng(a.seed)
    any_fail, per = 0, Counter()
    for _ in range(a.reps):
        g = groups.iloc[rng.permutation(len(groups))].reset_index(drop=True)
        _, fair = registry.evaluate(y, p, g)
        failed = [f"{kind}:{attr}" for attr, f in fair.items()
                  for kind, key in (("recall", "underserved_tpr"), ("auc", "underserved_auc"), ("calibration", "miscalibrated"))
                  if f[key]]
        any_fail += bool(failed)
        per.update(failed)
    rate = any_fail / a.reps
    se = math.sqrt(rate * (1 - rate) / a.reps)
    out = {"db": a.db, "alg": a.alg, "features": list(features.FEATURES), "n_holdout": int(len(y)), "reps": a.reps,
           "observed_failed_gates": sorted(k for k, v in observed.items() if not v),
           "null_any_fairness_gate_fails": round(rate, 3), "null_rate_95ci": [round(max(0, rate - 1.96 * se), 3),
                                                                            round(min(1, rate + 1.96 * se), 3)],
           "null_fail_count_by_gate": dict(per.most_common())}
    print(json.dumps(out, indent=1))
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
