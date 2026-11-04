"""Build a throw-away SQLite database with a chosen number of OPEN cases for load testing (synthetic, SYN- only).

    .venv/bin/python scripts/loadtest/seed_scale.py --open 5000 --data-dir /tmp/lt/5000

How it works (reproducible: fixed seeds, no randomness after the base seed):
1. Base: the app's own seeder (`kareshafaa.seed.seed`, models + services + HL7 replay) builds a fixed-seed cohort of
   --base-patients synthetic patients (no model training, no simulation). Replay is ~30 patients/s and only ~14 % of
   replayed cases stay open, so replaying 20,000 open cases would take over an hour.
2. Scale-up: whole patient "families" of the base (patient + every row that belongs to them through foreign keys:
   reports, observations, cases, steps, communications, consents, questionnaire responses, lessons, call tasks,
   service requests, appointments, care plans, recalls, escalations, ...) are copied with new ids through the
   SQLAlchemy model tables until the number of open cases reaches --open. Patients that only have closed cases are
   copied as well, so closed history stays proportional (--closed-ratio closed-only patients per patient with an
   open case, default 1.0). Every copied patient gets a new `SYN-L<copy>-...` MRN and no Emirates ID.
Audit events, users and sessions are not copied (the copies are bulk-loaded, not replayed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ltenv  # noqa: E402

EXCLUDE = {"users", "user_sessions", "signin_help_requests", "patient_invites", "ingest_messages", "audit_events",
           "login_attempts"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--open", type=int, required=True, help="target number of open cases")
    ap.add_argument("--data-dir", required=True, help="fresh temp dir (outside the repo), e.g. /tmp/lt/5000")
    ap.add_argument("--base-patients", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--closed-ratio", type=float, default=1.0)
    a = ap.parse_args()
    d = ltenv.setup(a.data_dir)
    if (d / "kareshafaa.db").exists():
        sys.exit(f"{d}/kareshafaa.db exists; use a fresh directory")

    from sqlalchemy import select, text
    from kareshafaa.db import get_engine
    from kareshafaa.models import Base
    from kareshafaa.seed import seed

    t0 = time.time()
    base = seed(a.base_patients, a.seed, train=False, simulate=False, verbose=False)
    t_base = time.time() - t0
    print(f"base seed: {base['cases']} cases, {base['open']} open, {t_base:.0f}s", flush=True)

    eng = get_engine()
    md = Base.metadata
    cloned: list = []
    names: set[str] = set()
    for t in md.sorted_tables:
        if t.name in EXCLUDE:
            continue
        if t.name == "patients" or any(fk.column.table.name in names for c in t.columns for fk in c.foreign_keys
                                       if fk.column.table.name != t.name):
            cloned.append(t)
            names.add(t.name)
    fkmap = {t.name: [(c.name, next(iter(c.foreign_keys)).column.table.name) for c in t.columns
                      if c.foreign_keys and next(iter(c.foreign_keys)).column.table.name in names] for t in cloned}

    rows: dict[str, list[dict]] = {}
    fam: dict[tuple[str, int], int] = {}
    with eng.connect() as conn:
        for t in cloned:
            rows[t.name] = [dict(r._mapping) for r in conn.execute(select(t).order_by(t.c.id))]
        # family = owning patient id, resolved through foreign keys (iterate: circular report<->case refs)
        for _ in range(4):
            for t in cloned:
                for r in rows[t.name]:
                    if (t.name, r["id"]) in fam:
                        continue
                    if t.name == "patients":
                        fam[(t.name, r["id"])] = r["id"]
                        continue
                    for col, ref in fkmap[t.name]:
                        v = r.get(col)
                        if v is not None and (ref, v) in fam:
                            fam[(t.name, r["id"])] = fam[(ref, v)]
                            break
        by_fam: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for t in cloned:
            for r in rows[t.name]:
                f = fam.get((t.name, r["id"]))
                if f is not None:
                    by_fam[f][t.name].append(r)
        open_by_fam = {f: sum(1 for c in g["cases"] if c["state"] != "closed") for f, g in by_fam.items()}
        # base users that sign in as a patient keep their own record; copy every synthetic patient family
        mrn = {p["id"]: p["mrn"] for p in rows["patients"]}
        open_fams = sorted(f for f, n in open_by_fam.items() if n and mrn[f].startswith("SYN-"))
        closed_fams = sorted(f for f, n in open_by_fam.items() if not n and mrn[f].startswith("SYN-"))
        base_open = sum(open_by_fam.values())
        next_id = {t.name: (conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {t.name}")).scalar() or 0) + 1
                   for t in cloned}

    plan: list[int] = []
    n_open = base_open
    i = 0
    while n_open < a.open and open_fams:
        f = open_fams[i % len(open_fams)]
        plan.append(f)
        n_open += open_by_fam[f]
        i += 1
    n_open_pat = len(plan)
    target_closed = int(round(a.closed_ratio * (n_open_pat + len(open_fams)))) - len(closed_fams)
    plan += [closed_fams[j % len(closed_fams)] for j in range(max(0, target_closed))] if closed_fams else []
    copy_no: dict[int, int] = defaultdict(int)

    out: dict[str, list[dict]] = defaultdict(list)
    for f in plan:
        copy_no[f] += 1
        c = copy_no[f]
        g = by_fam[f]
        ids: dict[tuple[str, int], int] = {}
        for tname, rs in g.items():
            for r in rs:
                ids[(tname, r["id"])] = next_id[tname]
                next_id[tname] += 1
        for t in cloned:
            for r in g.get(t.name, ()):
                nr = dict(r)
                nr["id"] = ids[(t.name, r["id"])]
                for col, ref in fkmap[t.name]:
                    v = r.get(col)
                    if v is not None:
                        nr[col] = ids.get((ref, v))
                if t.name == "patients":
                    nr["mrn"] = f"SYN-L{c:05d}-{r['mrn'][4:]}"[:40]
                    nr["national_id"] = None
                out[t.name].append(nr)

    t1 = time.time()
    with eng.begin() as conn:
        conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
        for t in cloned:
            rs = out.get(t.name, [])
            for k in range(0, len(rs), 5000):
                conn.execute(t.insert(), rs[k:k + 5000])
    t_load = time.time() - t1
    with eng.connect() as conn:
        counts = {t.name: conn.execute(text(f"SELECT COUNT(*) FROM {t.name}")).scalar() for t in cloned}
        open_now = conn.execute(text("SELECT COUNT(*) FROM cases WHERE state != 'closed'")).scalar()
        bad = conn.execute(text("SELECT COUNT(*) FROM patients WHERE mrn NOT LIKE 'SYN-%'")).scalar()
    summary = {"open_cases": open_now, "target_open": a.open, "base_open": base_open, "family_copies": len(plan),
               "non_syn_patients": bad, "base_seconds": round(t_base, 1), "copy_seconds": round(t_load, 1),
               "rows": {k: v for k, v in counts.items() if v}}
    (d / "seed_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
