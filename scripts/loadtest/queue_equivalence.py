"""Check the SQL work queue against the former Python path (tests/queue_oracle.py) on a whole database.

    .venv/bin/python scripts/loadtest/queue_equivalence.py --data-dir /tmp/lt-after/20000
    KS_PG_URL=... .venv/bin/python scripts/loadtest/queue_equivalence.py --url-env KS_PG_URL --seed 300 --data-dir /tmp/pgchk

--url-env names an environment variable holding a LOCAL throw-away PostgreSQL URL (never printed); --seed N first
builds a fresh synthetic database there with the app's seeder. Compares counts, totals, every row and the order
(exact sort-key ties: case id) for every staff user who can read the queue, all views, at several 'now' times, and
checks that the stored SLA thresholds equal a fresh recompute.
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ltenv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--url-env")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-users", type=int, default=0, help="0 = all queue users")
    a = ap.parse_args()
    ltenv.setup(a.data_dir)
    if a.url_env:
        url = os.environ[a.url_env]
        if not (url.startswith("postgresql") and ("localhost" in url or "127.0.0.1" in url or "host=/" in url)):
            sys.exit("refusing: --url-env must point at a local throw-away PostgreSQL")
        os.environ["KARESHAFAA_DATABASE_URL"] = url
    sys.path.insert(0, str(ltenv.REPO / "tests"))
    from sqlalchemy import select

    from kareshafaa import queue_store as qs
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.db import get_engine, init_db, session_factory
    from kareshafaa.models import User, utcnow
    from kareshafaa.security import has_perm
    from kareshafaa.services.queue import VIEWS, sort_key, work_queue
    from queue_oracle import work_queue_python

    if a.seed:
        from kareshafaa.seed import seed

        seed(a.seed, 11, train=False, simulate=False, verbose=False)
    else:
        init_db()
    print("dialect:", get_engine().dialect.name)
    db = session_factory()()
    stale = qs.refresh(db.connection(), None, write=False)
    print("stale stored thresholds:", stale)
    users = [actor_from_user(u) for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.id))
             if has_perm(u.role, "queue:read") and u.role != "patient"]
    if a.max_users:
        users = users[:a.max_users]
    t = utcnow()
    nows = [t, t - timedelta(days=3), t + timedelta(hours=20), t + timedelta(days=9), t + timedelta(days=60)]
    n = bad = 0
    t_old = t_new = 0.0
    for now in nows:
        for u in users:
            for v in VIEWS:
                t0 = time.perf_counter()
                old = work_queue_python(db, u, v, now=now, limit=10**6)
                t1 = time.perf_counter()
                new = work_queue(db, u, v, now=now, limit=10**6)
                t2 = time.perf_counter()
                t_old, t_new = t_old + t1 - t0, t_new + t2 - t1
                checks = {
                    "counts": old["counts"] == new["counts"] and old["total"] == new["total"],
                    "order": [sort_key(r) for r in old["items"]] == [sort_key(r) for r in new["items"]],
                    "rows": sorted(old["items"], key=lambda r: r["id"]) == sorted(new["items"], key=lambda r: r["id"]),
                    "ties": all(ids == sorted(ids) for ids in
                                ([r["id"] for r in g] for _, g in itertools.groupby(new["items"], key=sort_key))),
                }
                n += 1
                if not all(checks.values()):
                    bad += 1
                    if bad <= 3:
                        failed = [k for k, x in checks.items() if not x]
                        print("MISMATCH", u.role, v, now, failed)
                        if "ties" in failed:
                            for k, g in itertools.groupby(new["items"], key=sort_key):
                                ids = [r["id"] for r in g]
                                if ids != sorted(ids):
                                    print("   tie group", k, ids[:8])
                                    break
                        if "rows" in failed:
                            od = {r["id"]: r for r in old["items"]}
                            for r in new["items"]:
                                if od.get(r["id"]) != r:
                                    print("  ", {k: (od.get(r["id"], {}).get(k), r[k]) for k in r
                                                 if od.get(r["id"], {}).get(k) != r[k]})
                                    break
    db.close()
    print(f"comparisons: {n}, mismatches: {bad}, users: {len(users)}, views: {len(VIEWS)}, nows: {len(nows)}")
    print(f"time: python path {t_old:.1f}s, SQL path {t_new:.1f}s")
    return 1 if bad or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
