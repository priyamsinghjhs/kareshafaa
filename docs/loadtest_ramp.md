# Load ramp: breaking point (round 20, 21 Oct 2026)

`scripts/loadtest_ramp.py http://127.0.0.1:8001 --steps 5,10,20,40,80,160,320 --step-seconds 20`

## Setup

* **Server:** `python -m wasl.serve --workers 4`, running on a fresh copy of the largest database (`data/wasl_10k.db`,
  122 MB). The copy keeps the cohort unchanged.
* **Clients:** closed-loop virtual users with **zero think time**, so each user is a continuous stream of requests.
* **Mix:**

  | Request | Share |
  |---|---|
  | queue | 25 |
  | case detail | 25 |
  | overdue | 10 |
  | GP queue | 10 |
  | FHIR | 8 |
  | KPIs | 7 |
  | patient | 7 |
  | HL7 ingest (write) | 8 |

* **Breaking point:** p95 > 1 s or error rate > 1 %.
* **Measurement caveat:** the load generator ran on the same 8-CPU box as the server, so these figures are
  conservative.

## Results

| Concurrent users | Requests/s | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|---|---|---|---|---|---|
| 5 | 185 | 21 | 77 | 120 | 0 |
| 10 | 158 | 23 | 275 | 441 | 0 |
| 20 | 131 | 48 | 421 | 663 | 0 |
| 40 | 120 | 55 | 945 | 1,365 | 0 |
| 80 | 4 | 30,171 | 30,334 | 30,339 | 72 % (connection-pool timeouts) |

* **Capacity:** at most about 40 concurrent zero-think-time users and about 120–185 requests/s while p95 stays
  under 1 s. Throughput peaks at 5 users; as concurrency rises, SQLite writer serialisation and the queue queries
  (p95 about 1.1 s at 40 users) dominate.
* **Collapse at 80 users.** Each worker has a SQLAlchemy pool of 5 + 10 connections with a 30 s checkout timeout.
  When requests queue beyond that, they time out at 30 s.
  * The server recovered by itself once the backlog drained (about 1–2 min).
  * There was no connection leak: login worked again and the audit chain verified as valid.
  * Recommended before a pilot: Postgres with a pooler, plus admission control (fast 503 instead of a 30 s wait).
    **Not implemented.**
* **Fixed this round:** KPI cache stampede. Under constant ingest, every waiting request recomputed the KPIs in
  turn.

  | KPI p95 | Before fix | After fix |
  |---|---|---|
  | 20 users | 2.4 s | 48 ms |
  | 40 users | 6.0 s | 37 ms |

  The breaking point before the fix was also 40 users (p95 1,051 ms).
* **Human users have think time.** At an assumed ~10 s per action, 120 requests/s is roughly 1,200 concurrent staff.
  This is an estimate, not measured.
