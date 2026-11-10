#!/usr/bin/env python3
"""Migration rehearsal: upgrade an OLD-schema database with the CURRENT code and prove nothing was lost.

Migrations in KareShafaa are additive only (db.init_db: create_all + ALTER TABLE ADD COLUMN + indexes + append-only
triggers). This script rehearses that upgrade on a throwaway copy, never on a live database:

1. Build the old database: either seed one with the code at ``--base-ref`` (git archive of that commit, synthetic
   data, run in a temporary folder), or copy an existing SQLite file given with ``--db-copy`` (the original is
   never opened for writing).
2. Snapshot: tables, columns per table, row count per table, append-only triggers.
3. Run the CURRENT ``init_db`` against it (in a subprocess, so settings and engines are fresh), twice.
4. Check: no table or column removed, row counts in every pre-existing table unchanged, append-only triggers present
   and enforcing, audit hash chain valid, second run changes nothing (idempotent).

Optional PostgreSQL rehearsal: ``--postgres-env NAME`` names an environment variable holding the URL of a
THROWAWAY database. It is refused if it equals ``DATABASE_URL`` or ``KARESHAFAA_DATABASE_URL``, and it is wiped by
the old-code seed, so ``--wipe-postgres`` must also be given. The URL is never printed or logged.

Usage:
  python scripts/migration_rehearsal.py --base-ref master
  python scripts/migration_rehearsal.py --db-copy /path/to/kareshafaa.db
  THROWAWAY_PG_URL=... python scripts/migration_rehearsal.py --base-ref master --postgres-env THROWAWAY_PG_URL --wipe-postgres
Exit code 0 = passed, 1 = a check failed, 2 = refused / setup error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SNAPSHOT = r'''
import json, os
from sqlalchemy import create_engine, inspect, text
url = os.environ["KARESHAFAA_DATABASE_URL"]  # from the environment, never the command line
eng = create_engine(url)
insp = inspect(eng)
out = {"tables": {}, "triggers": []}
with eng.connect() as c:
    for t in sorted(insp.get_table_names()):
        cols = sorted(x["name"] for x in insp.get_columns(t))
        n = c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
        out["tables"][t] = {"columns": cols, "rows": int(n)}
    if eng.dialect.name == "sqlite":
        out["triggers"] = sorted(r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")))
    else:
        out["triggers"] = sorted(r[0] for r in c.execute(text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = 'audit_events'::regclass")))
print(json.dumps(out))
'''

UPGRADE = r'''
import json
from kareshafaa.db import init_db, session_scope
init_db()
from kareshafaa import audit
from sqlalchemy import text
with session_scope() as db:
    chain = audit.verify_chain(db)
blocked = False
from kareshafaa.db import get_engine
try:
    with get_engine().begin() as c:
        c.execute(text("UPDATE audit_events SET event = event WHERE id = (SELECT MIN(id) FROM audit_events)"))
except Exception:
    blocked = True
print(json.dumps({"chain": {k: v for k, v in chain.items() if k in ("valid", "checked", "broken_at_id")},
                  "append_only_enforced": blocked}, default=str))
'''


def _env(url: str, data_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KARESHAFAA_") and k not in ("DATABASE_URL", "RENDER")}
    env.update({"KARESHAFAA_DATABASE_URL": url, "KARESHAFAA_DATA_DIR": str(data_dir), "KARESHAFAA_MODE": "standard",
                "KARESHAFAA_PROVISION_DEMO_ACCOUNTS": "false", "KARESHAFAA_DEMO_CREDENTIALS_FILE": str(data_dir / "creds.txt"),
                "KARESHAFAA_MODEL_DIR": str(data_dir / "models"), "PYTHONHASHSEED": "0"})
    return env


def _run(args: list[str], cwd: Path, env: dict, what: str) -> str:
    r = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-15:])
        secret = env.get("KARESHAFAA_DATABASE_URL", "")
        if secret and not secret.startswith("sqlite"):
            tail = tail.replace(secret, "<database url>")
            from urllib.parse import urlsplit
            pw = urlsplit(secret).password
            if pw:
                tail = tail.replace(pw, "<password>")
        raise SystemExit(f"{what} failed (exit {r.returncode}):\n{tail}")
    return r.stdout


def _snapshot(env: dict) -> dict:
    return json.loads(_run([sys.executable, "-c", SNAPSHOT], ROOT, env, "snapshot").strip().splitlines()[-1])


def _compare(before: dict, after: dict) -> list[str]:
    problems = []
    for t, info in before["tables"].items():
        a = after["tables"].get(t)
        if a is None:
            problems.append(f"table removed: {t}")
            continue
        gone = sorted(set(info["columns"]) - set(a["columns"]))
        if gone:
            problems.append(f"columns removed from {t}: {', '.join(gone)}")
        if a["rows"] != info["rows"]:
            problems.append(f"row count changed in {t}: {info['rows']} -> {a['rows']}")
    for trig in before["triggers"]:
        if trig not in after["triggers"]:
            problems.append(f"trigger removed: {trig}")
    return problems


def _added(before: dict, after: dict) -> dict:
    return {"tables": sorted(set(after["tables"]) - set(before["tables"])),
            "columns": {t: sorted(set(a["columns"]) - set(before["tables"][t]["columns"]))
                        for t, a in after["tables"].items() if t in before["tables"]
                        and set(a["columns"]) - set(before["tables"][t]["columns"])}}


def rehearse(url: str, work: Path, base_ref: str | None, patients: int, db_copy: Path | None) -> dict:
    data_old, data_new = work / "data_old", work / "data_new"
    data_old.mkdir(parents=True, exist_ok=True)
    data_new.mkdir(parents=True, exist_ok=True)
    if db_copy is None:
        old = work / "old_code"
        old.mkdir()
        arch = subprocess.run(["git", "archive", base_ref, "kareshafaa"], cwd=ROOT, capture_output=True)
        if arch.returncode != 0:
            raise SystemExit(f"git archive {base_ref} failed: {arch.stderr.decode(errors='replace').strip()[:300]}")
        subprocess.run(["tar", "-x", "-C", str(old)], input=arch.stdout, check=True)
        _run([sys.executable, "-m", "kareshafaa.seed", "--patients", str(patients), "--no-train", "--no-sim"],
             old, _env(url, data_old), f"seeding with the code at {base_ref}")
    env_new = _env(url, data_new)
    before = _snapshot(env_new)
    first = json.loads(_run([sys.executable, "-c", UPGRADE], ROOT, env_new, "current init_db").strip().splitlines()[-1])
    after = _snapshot(env_new)
    _run([sys.executable, "-c", UPGRADE], ROOT, env_new, "current init_db (second run)")
    again = _snapshot(env_new)
    problems = _compare(before, after)
    if again != after:
        problems.append("second init_db run changed the schema or row counts (not idempotent)")
    chain = first["chain"]
    if not chain.get("valid", False):
        problems.append(f"audit hash chain not valid after upgrade: {chain}")
    if not first["append_only_enforced"]:
        problems.append("audit_events accepted an UPDATE after upgrade (append-only trigger missing)")
    return {"source": f"seeded at {base_ref}" if db_copy is None else "copy of a provided SQLite file",
            "tables_before": len(before["tables"]), "tables_after": len(after["tables"]),
            "rows_checked": sum(t["rows"] for t in before["tables"].values()),
            "added": _added(before, after), "audit_chain": chain,
            "append_only_enforced": first["append_only_enforced"], "problems": problems, "passed": not problems}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--base-ref", default="master", help="git ref whose code builds the old database (default master)")
    src.add_argument("--db-copy", type=Path, help="existing SQLite file to rehearse on (copied first; never modified)")
    ap.add_argument("--patients", type=int, default=40, help="synthetic patients for the old database (default 40)")
    ap.add_argument("--postgres-env", help="NAME of an env var holding a THROWAWAY PostgreSQL URL (never printed)")
    ap.add_argument("--wipe-postgres", action="store_true", help="confirm the throwaway PostgreSQL database may be wiped")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    a = ap.parse_args()
    pg_url = ""
    if a.postgres_env:
        if a.db_copy:
            print("--postgres-env needs --base-ref (an old database is seeded into it)", file=sys.stderr)
            return 2
        pg_url = os.environ.get(a.postgres_env, "").strip()
        live = {os.environ.get("DATABASE_URL", "").strip(), os.environ.get("KARESHAFAA_DATABASE_URL", "").strip()} - {""}
        if not pg_url:
            print(f"environment variable {a.postgres_env} is empty", file=sys.stderr)
            return 2
        if pg_url in live:
            print("refused: the PostgreSQL URL is the application's own database (DATABASE_URL)", file=sys.stderr)
            return 2
        if not a.wipe_postgres:
            print("refused: the PostgreSQL rehearsal wipes that database; add --wipe-postgres to confirm",
                  file=sys.stderr)
            return 2
    work = Path(tempfile.mkdtemp(prefix="kareshafaa-migration-"))
    report: dict = {}
    try:
        db = work / "rehearsal.db"
        if a.db_copy:
            if not a.db_copy.is_file():
                print(f"no such file: {a.db_copy}", file=sys.stderr)
                return 2
            shutil.copy2(a.db_copy, db)
        report["sqlite"] = rehearse(f"sqlite:///{db}", work / "sqlite", None if a.db_copy else a.base_ref,
                                    a.patients, a.db_copy)
        if a.postgres_env:
            from kareshafaa.config import normalise_database_url  # noqa: E402 - only needed here
            report["postgres"] = rehearse(normalise_database_url(pg_url), work / "pg", a.base_ref, a.patients, None)
        print(json.dumps(report, indent=2, default=str))
        return 0 if all(r["passed"] for r in report.values()) else 1
    finally:
        if a.keep:
            print(f"kept: {work}", file=sys.stderr)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
