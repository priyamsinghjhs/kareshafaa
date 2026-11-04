"""Dev tool: SQL statements and server time per API call (finds N+1 queries). Runs the app IN-PROCESS on a
database COPY given by KARESHAFAA_DATABASE_URL (SQLite file or a local PostgreSQL); never point it at a hosted one.

    KARESHAFAA_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:55432/copy python scripts/profile_queries.py [--json out.json]
Signs in with the test-account token API, so the copy must run in standard mode (the default here).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

os.environ.setdefault("KARESHAFAA_SWEEP_INTERVAL_SECONDS", "0")
os.environ["KARESHAFAA_MODE"] = "standard"
os.environ["KARESHAFAA_DEMO_LOGINS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from kareshafaa.db import get_engine  # noqa: E402
from kareshafaa.main import app  # noqa: E402

COUNT = {"n": 0}


def main() -> int:
    eng = get_engine()
    event.listen(eng, "before_cursor_execute", lambda *a, **k: COUNT.__setitem__("n", COUNT["n"] + 1))
    out = {}
    with TestClient(app) as c:
        def tok(u):
            r = c.post("/api/auth/demo-login", json={"username": u}); r.raise_for_status(); c.cookies.clear()
            return {"Authorization": f"Bearer {r.json()['token']}"}
        co, gp, lead, adm, pt = (tok(u) for u in ("aisha.rahman", "omar.haddad", "fatima.al.mansoori", "admin", "patient.en"))
        q = c.get("/api/queue?view=all&limit=50", headers=co).json()
        cid = q["items"][0]["id"]
        pid = c.get("/api/patients?q=SYN-", headers=co).json()["items"][0]["id"]
        plan = [
            ("queue (coordinator, 50)", "/api/queue?view=all&limit=50", co),
            ("queue overdue", "/api/queue?view=overdue&limit=50", co),
            ("queue review (GP)", "/api/queue?view=review&limit=50", gp),
            ("case detail", f"/api/cases/{cid}", co),
            ("patients search", "/api/patients?q=SYN-", co),
            ("patient record", f"/api/patients/{pid}", co),
            ("recalls upcoming", "/api/recalls?view=upcoming", co),
            ("care summaries", "/api/gp-summaries?view=all", gp),
            ("consults", "/api/consults", gp),
            ("impact report", "/api/analytics/impact", lead),
            ("kpis", "/api/analytics/kpis", lead),
            ("lessons", "/api/analytics/lessons", lead),
            ("governance models", "/api/governance/models", lead),
            ("governance proposals", "/api/governance/proposals", lead),
            ("rule cards", "/api/governance/rule-cards", lead),
            ("overrides", "/api/governance/overrides", lead),
            ("audit list", "/api/audit?limit=50", lead),
            ("admin users", "/api/admin/users", adm),
            ("admin system", "/api/admin/system", adm),
            ("patient app case", "/api/me/case", pt),
            ("page /queue", "/queue", co),
            ("page /cases/{id}", f"/cases/{cid}", co),
        ]
        for name, path, h in plan:
            times, counts, status = [], [], None
            for i in range(4):
                COUNT["n"] = 0
                t = time.perf_counter()
                r = c.get(path, headers=h)
                times.append((time.perf_counter() - t) * 1000)
                counts.append(COUNT["n"])
                status = r.status_code
            warm = times[1:]
            out[name] = {"path": path, "status": status, "queries": counts[-1], "first_ms": round(times[0], 1),
                         "warm_median_ms": round(statistics.median(warm), 1), "bytes": len(r.content)}
            print(f"{name:26s} {status} q={counts[-1]:4d} first={times[0]:7.1f}ms warm={statistics.median(warm):7.1f}ms "
                  f"{len(r.content) / 1024:7.1f} KB")
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
