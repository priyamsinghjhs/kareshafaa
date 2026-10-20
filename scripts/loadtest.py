"""Async HTTP load test (httpx). Reports p50/p95/p99 latency per endpoint.

Usage: python scripts/loadtest.py [base_url] [requests_per_endpoint] [concurrency]
Runs against a server seeded with synthetic data (e.g. 10k patients). Mostly reads; the last phase ingests
synthetic HL7 ORU messages (writes). Every request is audited, as in production, and the audit hash chain
is verified at the end.
"""
from __future__ import annotations

import asyncio
import random
import statistics
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 200
CONC = int(sys.argv[3]) if len(sys.argv) > 3 else 10


async def login(c: httpx.AsyncClient, user: str) -> dict:
    r = await c.post("/api/auth/demo-login", json={"username": user})
    r.raise_for_status()
    c.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        co, gp, lead, an, pt = [await login(c, u) for u in
                                ("aisha.rahman", "omar.haddad", "fatima.al.mansoori", "analyst", "patient.ar")]
        q = (await c.get("/api/queue?view=all&limit=1000", headers=lead)).json()
        open_ids = [i["id"] for i in q["items"]]
        all_n = (await c.get("/api/cases?limit=1", headers=lead)).json()
        print(f"open cases visible to clinical lead: {q['total']}")
        plan = {
            "GET /api/queue (coordinator, page 50)": lambda: ("/api/queue?view=all&limit=50", co),
            "GET /api/queue?view=overdue (coordinator)": lambda: ("/api/queue?view=overdue&limit=50", co),
            "GET /api/queue (GP, org-scoped)": lambda: ("/api/queue?view=all&limit=50", gp),
            "GET /api/cases/{id} (detail)": lambda: (f"/api/cases/{random.choice(open_ids)}", lead),
            "GET /api/cases/{id}/fhir": lambda: (f"/api/cases/{random.choice(open_ids)}/fhir", lead),
            "GET /api/analytics/kpis (cached)": lambda: ("/api/analytics/kpis", an),
            "GET /api/me/case (patient)": lambda: ("/api/me/case", pt),
        }
        _ = all_n
        samples = [(await c.get("/api/ingest/sample", headers=co)).text for _ in range(N + 1)]
        plan["POST /api/ingest/hl7v2 (write: new case)"] = lambda: ("/api/ingest/hl7v2", co, samples.pop())
        sem = asyncio.Semaphore(CONC)
        results: dict[str, list[float]] = {}
        errors: dict[str, int] = {}

        async def one(name, fn):
            url, h, *body = fn()
            async with sem:
                t = time.perf_counter()
                if body:
                    r = await c.post(url, headers={**h, "Content-Type": "text/plain"}, content=body[0])
                else:
                    r = await c.get(url, headers=h)
                dt = (time.perf_counter() - t) * 1000
            results.setdefault(name, []).append(dt)
            if r.status_code != 200:
                errors[name] = errors.get(name, 0) + 1

        for name, fn in plan.items():
            await one(name, fn)  # warm-up
            results[name] = []
            t0 = time.perf_counter()
            await asyncio.gather(*(one(name, fn) for _ in range(N)))
            wall = time.perf_counter() - t0
            xs = results[name]
            print(f"{name:45s} n={len(xs):4d} p50={pct(xs, 50):7.1f}ms p95={pct(xs, 95):7.1f}ms "
                  f"p99={pct(xs, 99):7.1f}ms mean={statistics.mean(xs):7.1f}ms  {len(xs) / wall:6.1f} req/s"
                  f"  errors={errors.get(name, 0)}")


        lead_v = (await c.get("/api/audit/verify", headers=lead)).json()
        print(f"audit hash chain after load: valid={lead_v.get('valid')} checked={lead_v.get('checked')}")


if __name__ == "__main__":
    asyncio.run(main())
