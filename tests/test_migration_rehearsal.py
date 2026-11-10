"""Migration rehearsal script: an old-schema database (seeded with the code at an earlier commit) upgrades with
the current init_db without losing tables, columns or rows; the PostgreSQL option refuses the app's own database."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "migration_rehearsal.py"


def _ref_exists(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=ROOT, capture_output=True).returncode == 0


@pytest.mark.skipif(not (ROOT / ".git").exists() or not _ref_exists("08926e7"), reason="needs the git history")
def test_rehearsal_from_an_older_commit_passes():
    r = subprocess.run([sys.executable, str(SCRIPT), "--base-ref", "08926e7", "--patients", "20"], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    rep = json.loads(r.stdout)["sqlite"]
    assert rep["passed"] and not rep["problems"]
    assert "critical_read_backs" in rep["added"]["tables"] and "channel" in rep["added"]["columns"]["escalations"]
    assert rep["audit_chain"]["valid"] and rep["append_only_enforced"]


def test_postgres_option_refuses_the_application_database():
    env = dict(os.environ, THROWAWAY="postgresql://u:secretpw@h/db", DATABASE_URL="postgresql://u:secretpw@h/db")
    r = subprocess.run([sys.executable, str(SCRIPT), "--postgres-env", "THROWAWAY", "--wipe-postgres"], cwd=ROOT,
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and "refused" in r.stderr
    assert "secretpw" not in r.stderr + r.stdout


def test_postgres_option_needs_wipe_confirmation():
    env = dict(os.environ, THROWAWAY="postgresql://u:secretpw@h/other")
    env.pop("DATABASE_URL", None)
    r = subprocess.run([sys.executable, str(SCRIPT), "--postgres-env", "THROWAWAY"], cwd=ROOT,
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and "--wipe-postgres" in r.stderr and "secretpw" not in r.stderr
