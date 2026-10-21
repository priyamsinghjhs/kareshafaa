"""Ramp load test: find the breaking point (round 20).

Closed-loop virtual users with ZERO think time (worst case: every user fires its next request as soon as the last
one returns) on a mixed staff/patient workload. Concurrency doubles each step; each step runs for --step-seconds.
Breaking point = first step where p95 > --p95-limit ms or the error rate > 1 %.

Usage: python scripts/loadtest_ramp.py http://127.0.0.1:8001 --steps 5,10,20,40,80,160 --step-seconds 20
The load generator runs on the same machine as the server, so it competes for CPU: results are a LOWER bound.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import httpx

MIX = [("queue", 25), ("detail", 25), ("overdue", 10), ("gp_queue", 10), ("fhir", 8), ("kpis", 7), ("patient", 7),
       ("ingest", 8)]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else float("nan")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--steps", default="5,10,20,40,80,160")
    ap.add_argument("--step-seconds", type=float, default=20)
    ap.add_argument("--p95-limit", type=float, default=1000)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=1000)
    async with httpx.AsyncClient(base_url=a.base, timeout=30, limits=limits) as c:
        hdr = {}
        for u in ("aisha.rahman", "omar.haddad", "fatima.al.mansoori", "analyst", "patient.ar"):
            r = await c.post("/api/auth/demo-login", json={"username": u})
            if r.status_code != 200:
                continue
            hdr[u] = {"Authorization": f"Bearer {r.json()['token']}"}
            c.cookies.clear()
        co, gp, lead = hdr["aisha.rahman"], hdr["omar.haddad"], hdr["fatima.al.mansoori"]
        an, pt = hdr.get("analyst", lead), hdr.get("patient.ar")
        ids = [i["id"] for i in (await c.get("/api/queue?view=all&limit=1000", headers=lead)).json()["items"]]
        samples = [(await c.get("/api/ingest/sample", headers=co)).text for _ in range(3000)]
        names, weights = zip(*MIX)

        def req(kind):
            if kind == "queue":
                return "GET", "/api/queue?view=all&limit=50", co, None
            if kind == "overdue":
                return "GET", "/api/queue?view=overdue&limit=50", co, None
            if kind == "gp_queue":
                return "GET", "/api/queue?view=all&limit=50", gp, None
            if kind == "detail":
                return "GET", f"/api/cases/{random.choice(ids)}", lead, None
            if kind == "fhir":
                return "GET", f"/api/cases/{random.choice(ids)}/fhir", lead, None
            if kind == "kpis":
                return "GET", "/api/analytics/kpis", an, None
            if kind == "patient" and pt:
                return "GET", "/api/me/case", pt, None
            return "POST", "/api/ingest/hl7v2", co, samples.pop() if samples else None

        rows = []
        for users in [int(x) for x in a.steps.split(",")]:
            lat, errs, by_kind = [], 0, {}
            stop = time.perf_counter() + a.step_seconds

            async def vu():
                nonlocal errs
                while time.perf_counter() < stop:
                    kind = random.choices(names, weights)[0]
                    m, url, h, body = req(kind)
                    if m == "POST" and body is None:
                        m, url, h, body = req("queue")
                    t = time.perf_counter()
                    try:
                        if m == "POST":
                            r = await c.post(url, headers={**h, "Content-Type": "text/plain"}, content=body)
                        else:
                            r = await c.get(url, headers=h)
                        ok = r.status_code == 200
                    except httpx.HTTPError:
                        ok = False
                    dt = (time.perf_counter() - t) * 1000
                    lat.append(dt)
                    by_kind.setdefault(kind, []).append(dt)
                    errs += 0 if ok else 1

            t0 = time.perf_counter()
            await asyncio.gather(*(vu() for _ in range(users)))
            wall = time.perf_counter() - t0
            row = {"users": users, "requests": len(lat), "rps": round(len(lat) / wall, 1), "p50": round(pct(lat, 50), 1),
                   "p95": round(pct(lat, 95), 1), "p99": round(pct(lat, 99), 1), "errors": errs,
                   "error_rate": round(errs / max(1, len(lat)), 4),
                   "p95_by_kind": {k: round(pct(v, 95), 1) for k, v in by_kind.items()}}
            rows.append(row)
            print(json.dumps(row), flush=True)
            if row["p95"] > a.p95_limit or row["error_rate"] > 0.01:
                print(f"BREAKING POINT at {users} concurrent zero-think-time users", flush=True)
                break
            await asyncio.sleep(2)
        if a.out:  # write the measurements first: the audit check below can be slow on a big chain
            json.dump({"steps": rows, "audit_valid": None}, open(a.out, "w"), indent=1)
        await asyncio.sleep(10)  # let queued requests drain before verifying
        try:
            r = await c.get("/api/audit/verify", headers=lead, timeout=300)
            v = r.json() if r.status_code == 200 else {"valid": None, "error": r.status_code}
        except (httpx.HTTPError, ValueError) as e:
            v = {"valid": None, "error": repr(e)}
        print(f"audit hash chain after load: valid={v.get('valid')} checked={v.get('checked')} {v.get('error', '')}")
        if a.out:
            json.dump({"steps": rows, "audit_valid": v.get("valid"), "audit_checked": v.get("checked")},
                      open(a.out, "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
