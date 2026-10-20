"""Full-cohort check (d): the 20,000-person realistic database must have ZERO invariant violations and ZERO
lost actionable results. Builds with `make seed-large` (~3 min); skipped if that database does not exist.
Run:  make test-cohort   (or pytest -m cohort)"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LARGE = ROOT / "data" / "wasl_large.db"

pytestmark = pytest.mark.cohort


@pytest.mark.skipif(not LARGE.exists(), reason="run `make seed-large` first")
def test_full_cohort_zero_violations():
    env = {**os.environ, "WASL_DATABASE_URL": f"sqlite:///{LARGE}", "WASL_MODEL_DIR": str(ROOT / "data" / "models_large")}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_data.py"), "--json"], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=900)
    rep = json.loads(out.stdout)
    assert rep["checked"]["patient"] >= 20000
    assert rep["checked"]["rules_reapplied"] >= 20000
    lost = rep["violations"].get("report.rules_reapplied_no_lost_actionable", {}).get("count", 0)
    assert lost == 0, "lost actionable results"
    assert rep["violations_total"] == 0, json.dumps(rep["violations"], indent=1, default=str)[:3000]


@pytest.mark.cohort
def test_full_cohort_alerts_zero_missed_breaches():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    db = root / "data" / "wasl_large.db"
    if not db.exists():
        pytest.skip("run `make seed-large` first")
    r = subprocess.run([sys.executable, "scripts/alert_eval.py", "--db", f"sqlite:///{db}", "--out", ""], cwd=root,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:]
