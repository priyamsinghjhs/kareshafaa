# Load test with Locust (3 Nov 2026)

## How it was run

```bash
/tmp/lt.sh <workers> <users> 60s   # local helper: fresh COPY of the local database, then
locust -f scripts/locustfile.py --host http://127.0.0.1:8043 --headless -u <users> -r 5 -t 60s --csv <out>
```

* **Server:** `python -m kareshafaa.serve --workers 1` (same as the free hosting plan) and `--workers 4`, on a fresh
  copy of the local database (about 2,000 synthetic patients, SQLite), sweep every 300 s, every request audited.
* **Users** (`scripts/locustfile.py`): coordinators 6 : clinical lead 2 : integration engine (HL7 v2 posts) 1 :
  patients 1, plus one readiness probe. Think time 0.5–5 s depending on the user type.
* **Machine:** one 8-CPU Linux box running both the server and Locust, so the figures include client-side delay and
  are **not** a production sizing. The hosted service (Render free plan) was not load tested.
* Never run this against the hosted service: it creates cases.

## 1 worker, 20 users, 60 s

| Request | Count | Failures | p50 ms | p95 ms | p99 ms | req/s |
|---|---|---|---|---|---|---|
| GET /api/analytics/impact | 42 | 0 | 9 | 150 | 210 | 0.7 |
| GET /api/analytics/impact?programme | 10 | 0 | 150 | 410 | 410 | 0.2 |
| GET /api/analytics/kpis | 27 | 0 | 7 | 320 | 390 | 0.5 |
| GET /api/cases/{id} | 208 | 0 | 16 | 38 | 97 | 3.5 |
| GET /api/governance/rule-cards | 12 | 0 | 9 | 25 | 25 | 0.2 |
| GET /api/ingest/sample | 56 | 0 | 9 | 24 | 33 | 0.9 |
| GET /api/me/case | 32 | 0 | 11 | 55 | 110 | 0.5 |
| GET /api/queue | 211 | 0 | 13 | 62 | 130 | 3.6 |
| GET /api/queue?view=overdue | 107 | 0 | 13 | 30 | 150 | 1.8 |
| GET /readyz | 12 | 0 | 7 | 24 | 24 | 0.2 |
| POST /api/ingest/hl7v2 | 56 | 0 | 17 | 43 | 61 | 0.9 |
| POST sign in | 19 | 0 | 27 | 49 | 49 | 0.3 |
| **All requests** | 792 | 0 | 14 | 55 | 180 | 13.4 |

## 4 workers, 100 users, 60 s

| Request | Count | Failures | p50 ms | p95 ms | p99 ms | req/s |
|---|---|---|---|---|---|---|
| GET /api/analytics/impact | 161 | 0 | 8 | 280 | 490 | 2.7 |
| GET /api/analytics/impact?programme | 56 | 0 | 140 | 390 | 850 | 0.9 |
| GET /api/analytics/kpis | 111 | 0 | 8 | 270 | 350 | 1.9 |
| GET /api/cases/{id} | 928 | 0 | 17 | 59 | 220 | 15.7 |
| GET /api/governance/rule-cards | 64 | 0 | 8 | 15 | 35 | 1.1 |
| GET /api/ingest/sample | 244 | 0 | 10 | 26 | 130 | 4.1 |
| GET /api/me/case | 149 | 0 | 10 | 35 | 110 | 2.5 |
| GET /api/queue | 944 | 0 | 15 | 55 | 150 | 16.0 |
| GET /api/queue?view=overdue | 490 | 0 | 15 | 49 | 170 | 8.3 |
| GET /readyz | 12 | 1 | 8 | 120 | 120 | 0.2 |
| POST /api/ingest/hl7v2 | 244 | 0 | 19 | 63 | 130 | 4.1 |
| POST sign in | 99 | 0 | 21 | 58 | 180 | 1.7 |
| **All requests** | 3502 | 1 | 16 | 69 | 240 | 59.3 |

The one failure in the 100-user run was a connection reset on the readiness probe, seen once and not repeated.

## Reading the results

* Work-queue and case pages stay under 70 ms at the 95th centile at 100 concurrent users on 4 workers.
* The impact report is cached per programme; the first request after the cache expires takes 0.1–0.9 s, later ones
  under 10 ms (p50).
* HL7 v2 ingest (a write that creates a case, runs the rules and writes audit rows) stays under 65 ms at p95.
* Heavier ramp-up tests up to 160 users, with the overload limits, are in `docs/loadtest_ramp.md`.
