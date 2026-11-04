"""HTTP latency against a running local server (httpx + asyncio): sequential p50/p95 per endpoint, then the work
queue's first page under N concurrent users.

    .venv/bin/python scripts/loadtest/http_load.py --base http://127.0.0.1:8091 --data-dir /tmp/lt/5000 \
        --label cache2s [--seq 30] [--conc 10,25] [--per-user 6]

Signs in through the normal password login (login-page CSRF token + POST /api/auth/login) with the local
credentials file in the data dir; passwords are never printed. Results go to <data-dir>/http_<label>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ltenv  # noqa: E402

ROLES = {"coordinator": "aisha.rahman", "gp": "omar.haddad", "lead": "fatima.al.mansoori"}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1) if xs else float("nan")


async def login(base: str, user: str, pw: str) -> dict:
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        await c.get("/login")
        r = await c.post("/api/auth/login", json={"username": user, "password": pw},
                         headers={"x-csrf-token": c.cookies.get("kareshafaa_csrf") or ""})
        r.raise_for_status()
        return {"Authorization": f"Bearer {c.cookies.get('kareshafaa_session')}"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8091")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--label", default="run")
    ap.add_argument("--seq", type=int, default=30)
    ap.add_argument("--conc", default="10,25")
    ap.add_argument("--per-user", type=int, default=6, help="queue requests per concurrent user")
    a = ap.parse_args()
    creds = ltenv.read_credentials(a.data_dir)
    h = {k: await login(a.base, u, creds[u]) for k, u in ROLES.items()}
    res: dict = {"label": a.label, "sequential": {}, "concurrent": {}}
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=100)
    async with httpx.AsyncClient(base_url=a.base, timeout=120, limits=limits) as c:
        q = (await c.get("/api/queue?view=all&limit=50", headers=h["coordinator"])).json()
        cid = q["items"][0]["id"]
        res["open_visible_coordinator"] = q["total"]
        plan = [
            ("GET /api/queue (coordinator, page 50)", "/api/queue?view=all&limit=50", "coordinator"),
            ("GET /api/queue?view=review (GP)", "/api/queue?view=review&limit=50", "gp"),
            ("GET /api/cases/{id}", f"/api/cases/{cid}", "coordinator"),
            ("GET /api/analytics/kpis (dashboard)", "/api/analytics/kpis", "lead"),
            ("GET /api/analytics/impact (dashboard)", "/api/analytics/impact", "lead"),
            ("GET /api/patients?q=SYN-", "/api/patients?q=SYN-", "coordinator"),
            ("GET /api/recalls?view=upcoming", "/api/recalls?view=upcoming", "coordinator"),
            ("GET /queue (HTML)", "/queue", "coordinator"),
            ("GET /cases/{id} (HTML)", f"/cases/{cid}", "coordinator"),
            ("GET /dashboard (HTML)", "/dashboard", "lead"),
        ]
        for name, url, role in plan:
            await c.get(url, headers=h[role])
            xs, errs = [], 0
            for _ in range(a.seq):
                t = time.perf_counter()
                r = await c.get(url, headers=h[role])
                xs.append((time.perf_counter() - t) * 1000)
                errs += r.status_code != 200
            res["sequential"][name] = {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "errors": errs}
            print(f"seq  {name:42s} p50={pct(xs, 50):8.1f} p95={pct(xs, 95):8.1f} err={errs}", flush=True)

        for conc in [int(x) for x in a.conc.split(",") if x]:
            xs: list[float] = []
            codes: dict[int, int] = {}

            async def user() -> None:
                for _ in range(a.per_user):
                    t = time.perf_counter()
                    try:
                        r = await c.get("/api/queue?view=all&limit=50", headers=h["coordinator"])
                        code = r.status_code
                    except httpx.HTTPError:
                        code = -1
                    dt = (time.perf_counter() - t) * 1000
                    codes[code] = codes.get(code, 0) + 1
                    if code == 200:
                        xs.append(dt)

            t0 = time.perf_counter()
            await asyncio.gather(*(user() for _ in range(conc)))
            wall = time.perf_counter() - t0
            n = sum(codes.values())
            res["concurrent"][str(conc)] = {"requests": n, "ok": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95),
                                            "status": {str(k): v for k, v in sorted(codes.items())},
                                            "req_per_s": round(n / wall, 1)}
            print(f"conc {conc:3d} queue p50={pct(xs, 50):8.1f} p95={pct(xs, 95):8.1f} ok={len(xs)}/{n} "
                  f"status={codes} {n / wall:.1f} req/s", flush=True)
    Path(a.data_dir, f"http_{a.label}.json").write_text(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
