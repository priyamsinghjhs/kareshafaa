"""Round 20: rules engine vs the independent reference on the EDGE-COHORT input distribution.

Re-creates the planned edge cohort (same seed: programme, severity category, scenario, patient context) and
replays every person's result through both implementations, with --draws fresh scenario mutations per person
(missing/unknown values, unit mismatches, amended/preliminary status...). Same comparison classes as
scripts/differential_test.py.

usage: python scripts/differential_edge.py --n 20000 --seed 2020 --draws 50
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from differential_test import compare, upper95  # noqa: E402

from wasl.synth.edge import _mutate, plan_cohort  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=2020)
    ap.add_argument("--draws", type=int, default=50)
    a = ap.parse_args()
    rng = random.Random(a.seed + 1)
    counts, ex = Counter(), {}
    for sp, plan in plan_cohort(a.n, a.seed):
        ctx = {"age": sp.age, "sex": sp.gender, "pregnant": sp.pregnant, "deceased": sp.deceased,
               "under_specialist_care": sp.specialist_care, "haemoglobinopathy": sp.haemoglobinopathy}
        for i in range(a.draws):
            r = _mutate(rng, plan.programme, plan.result, plan.scenario) if i else plan.result
            res = {k: v for k, v in asdict(r).items() if k != "defect"}
            cls, info = compare(plan.programme, res, ctx)
            counts[cls] += 1
            if cls != "agree" and len(ex.setdefault(cls, [])) < 5:
                ex[cls].append((plan.programme, res, ctx, info))
    n = sum(counts.values())
    safety = sum(v for k, v in counts.items() if k.startswith("SAFETY"))
    print({"comparisons": n, "classes": dict(counts), "safety_critical": safety,
           "safety_upper95": upper95(safety, n)})
    for k, v in ex.items():
        print(k, v[:3])


if __name__ == "__main__":
    main()
