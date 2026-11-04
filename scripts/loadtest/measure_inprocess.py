"""In-process measurements on a load-test database (no network): SQL statements and server time per endpoint,
the work_queue() service call on its own, and generate_proposals() runtime / peak memory / SQL statements.

    .venv/bin/python scripts/loadtest/measure_inprocess.py --data-dir /tmp/lt/5000 [--out /tmp/lt/5000/inprocess.json]

Signs in through the normal password login (POST /api/auth/login with the login-page CSRF token) using the local
credentials file the app writes into the data dir; passwords are never printed. The queue cache is switched off
(KARESHAFAA_QUEUE_CACHE_TTL_S=0) so every call does the full work. generate_proposals runs inside a transaction
that is rolled back, so repeated runs see the same data.
"""
from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ltenv  # noqa: E402

COUNT = {"n": 0}
ROLES = {"coordinator": "aisha.rahman", "gp": "omar.haddad", "lead": "fatima.al.mansoori", "admin": "admin",
         "analyst": "analyst"}


def rss_kb(field: str = "VmRSS") -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    return 0


def reset_peak_rss() -> None:
    try:
        Path("/proc/self/clear_refs").write_text("5")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out")
    ap.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args()
    d = ltenv.setup(a.data_dir, queue_cache_ttl=0)

    from fastapi.testclient import TestClient
    from sqlalchemy import event, func, select

    from kareshafaa.db import get_engine, session_factory
    from kareshafaa.main import app
    from kareshafaa.models import Case, User
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.services.improve import generate_proposals
    from kareshafaa.services.queue import work_queue

    eng = get_engine()
    event.listen(eng, "before_cursor_execute", lambda *x, **k: COUNT.__setitem__("n", COUNT["n"] + 1))
    res: dict = {"data_dir": str(d)}
    with TestClient(app) as c:
        creds = ltenv.read_credentials(d)

        def login(user: str) -> dict:
            c.cookies.clear()
            c.get("/login")
            r = c.post("/api/auth/login", json={"username": user, "password": creds[user]},
                       headers={"x-csrf-token": c.cookies.get("kareshafaa_csrf") or ""})
            r.raise_for_status()
            tok = c.cookies.get("kareshafaa_session")
            c.cookies.clear()
            return {"Authorization": f"Bearer {tok}"}

        h = {k: login(u) for k, u in ROLES.items()}
        q = c.get("/api/queue?view=all&limit=50", headers=h["coordinator"]).json()
        res["open_visible_coordinator"] = q["total"]
        cid = q["items"][0]["id"]
        plan = [
            ("GET /api/queue (coordinator, page 50)", "/api/queue?view=all&limit=50", "coordinator"),
            ("GET /api/queue?view=overdue", "/api/queue?view=overdue&limit=50", "coordinator"),
            ("GET /api/queue?view=review (GP)", "/api/queue?view=review&limit=50", "gp"),
            ("GET /api/cases/{id}", f"/api/cases/{cid}", "coordinator"),
            ("GET /api/analytics/kpis (dashboard)", "/api/analytics/kpis", "lead"),
            ("GET /api/analytics/impact (dashboard)", "/api/analytics/impact", "lead"),
            ("GET /api/patients?q=SYN-", "/api/patients?q=SYN-", "coordinator"),
            ("GET /api/recalls?view=upcoming", "/api/recalls?view=upcoming", "coordinator"),
            ("GET /queue (HTML)", "/queue", "coordinator"),
            ("GET /cases/{id} (HTML)", f"/cases/{cid}", "coordinator"),
            ("GET /dashboard (HTML)", "/dashboard", "lead"),
            ("GET /patients (HTML)", "/patients", "coordinator"),
            ("GET /recalls (HTML)", "/recalls", "coordinator"),
        ]
        ep = {}
        for name, url, role in plan:
            c.get(url, headers=h[role])  # warm-up (KPI cache etc. as in normal use)
            stmts, ms, codes = [], [], set()
            for _ in range(a.repeat):
                COUNT["n"] = 0
                t = time.perf_counter()
                r = c.get(url, headers=h[role])
                ms.append((time.perf_counter() - t) * 1000)
                stmts.append(COUNT["n"])
                codes.add(r.status_code)
            ep[name] = {"sql": max(stmts), "median_ms": round(statistics.median(ms), 1), "status": sorted(codes)}
            print(f"{name:42s} sql={max(stmts):4d} median={statistics.median(ms):8.1f} ms {sorted(codes)}", flush=True)
        res["endpoints"] = ep

    # service calls on their own
    Session = session_factory()
    with Session() as db:
        u = db.scalar(select(User).where(User.username == ROLES["coordinator"]))
        co = actor_from_user(u)
        adm = actor_from_user(db.scalar(select(User).where(User.username == "admin")))
        res["open_cases"] = db.scalar(select(func.count()).select_from(Case).where(Case.state != "closed"))
        wq = []
        for _ in range(a.repeat):
            COUNT["n"] = 0
            t = time.perf_counter()
            work_queue(db, co, view="all", limit=50)
            wq.append(((time.perf_counter() - t) * 1000, COUNT["n"]))
        gc.collect()
        reset_peak_rss()
        r0 = rss_kb()
        tracemalloc.start()
        work_queue(db, co, view="all", limit=50)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        res["work_queue"] = {"median_ms": round(statistics.median(x[0] for x in wq), 1), "sql": max(x[1] for x in wq),
                             "tracemalloc_peak_mb": round(peak / 2**20, 1),
                             "rss_peak_delta_mb": round((rss_kb("VmHWM") - r0) / 1024, 1)}
        db.rollback()
        # dashboard aggregates WITHOUT the per-process aggregate cache (what the first viewer after a change pays)
        from kareshafaa.services import analytics
        from kareshafaa.services.impact import impact_report
        for key, fn in (("kpis_uncached", lambda: analytics.kpis(db)), ("impact_uncached", lambda: impact_report(db))):
            xs = []
            for _ in range(3):
                COUNT["n"] = 0
                t = time.perf_counter()
                fn()
                xs.append(((time.perf_counter() - t) * 1000, COUNT["n"]))
            res[key] = {"median_ms": round(statistics.median(x[0] for x in xs), 1), "sql": max(x[1] for x in xs)}
            print(key, res[key], flush=True)
        db.rollback()

    gp = []
    for _ in range(a.repeat):
        with Session() as db:
            COUNT["n"] = 0
            t = time.perf_counter()
            n = len(generate_proposals(db, adm))
            gp.append(((time.perf_counter() - t) * 1000, COUNT["n"], n))
            db.rollback()
    gc.collect()
    with Session() as db:
        reset_peak_rss()
        r0 = rss_kb()
        tracemalloc.start()
        generate_proposals(db, adm)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        hwm = rss_kb("VmHWM")
        db.rollback()
    res["generate_proposals"] = {"median_ms": round(statistics.median(x[0] for x in gp), 1),
                                 "p95_ms": round(max(x[0] for x in gp), 1), "sql": max(x[1] for x in gp),
                                 "proposals": gp[0][2], "tracemalloc_peak_mb": round(peak / 2**20, 1),
                                 "rss_peak_delta_mb": round((hwm - r0) / 1024, 1)}
    print("work_queue()", res["work_queue"])
    print("generate_proposals()", res["generate_proposals"])
    out = Path(a.out) if a.out else d / "inprocess.json"
    out.write_text(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
