# Load ramp and graceful degradation (rounds 20–21, 21 Oct 2026)

## How the test was run

`scripts/loadtest_ramp.py http://127.0.0.1:8001 --steps 5,10,20,40,80,160 --step-seconds 20 --continue-past-break`

* **Server:** `python -m wasl.serve --workers 4`, on a fresh copy of the largest database (`data/wasl_10k.db`,
  122 MB) for each run.
* **Clients:** closed-loop virtual users with zero think time. The mix is:

  | Request type | Share |
  |---|---|
  | queue | 25 |
  | case detail | 25 |
  | overdue | 10 |
  | GP queue | 10 |
  | FHIR | 8 |
  | KPIs | 7 |
  | patient | 7 |
  | HL7 ingest (write) | 8 |

* **Breaking point:** p95 above 1 s, or more than 1 % non-200 responses.
* **Measurement caveat:** the load generator is a single Python process on the **same 8-CPU box**. At 160 users
  part of the measured latency is client-side, so all figures are conservative.
* **Raw data:**
  * `docs/loadtest_ramp.json`: round 21, default settings;
  * `docs/loadtest_ramp_inflight10.json`: 10 in-flight requests per worker;
  * `docs/loadtest_ramp_postgres.json`: PostgreSQL 17 smoke test.

## Before and after

Column key:
* **rps:** all responses per second.
* **ok/s:** 200 responses per second (goodput).
* **p95 ok:** p95 latency of the 200 responses.
* **p95 503:** p95 latency of the 503 responses.
* **max:** slowest single response.

| Users | Round 20: rps | p50 | p95 | errors | Round 21 (default): ok/s | p50 | p95 ok | p95 503 | max | non-200 |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 185 | 21 ms | 77 ms | 0 | 176 | 22 ms | 74 ms | – | 1.2 s | 0 |
| 10 | 158 | 23 ms | 275 ms | 0 | 185 | 42 ms | 139 ms | – | 1.8 s | 0 |
| 20 | 131 | 48 ms | 421 ms | 0 | 177 | 85 ms | 281 ms | – | 1.9 s | 0 |
| 40 | 120 | 55 ms | **945 ms** | 0 | 170 | 196 ms | **539 ms** | – | 3.4 s | 0 |
| 80 | **4** | **30.2 s** | **30.3 s** | 72 % (30 s pool timeouts) | **142** | 319 ms | 1.57 s | 644 ms | 4.1 s | 11.8 % (503) |
| 160 | not reached | | | | 75 | 1.19 s | 4.6 s | 5.2 s | 10.3 s | 16.7 % (503) |

**What changed:**
* **Sub-1 s capacity.** p95 is now 539 ms at 40 users (was 945 ms), with no errors and 40 % more goodput
  (170 vs 120 per second). 80 users is still above 1 s p95.
* **No collapse at 80 users.** Goodput stays around 140 per second instead of 4. Nothing waits 30 s, and every
  run ended with the audit chain verified as valid.
* **Not achieved: fast 503s at 160 users.** The server stays up and serves about 75–115 per second, but 503s take
  up to about 5 s (p95). At that point the event loops and the same-box load generator are CPU-saturated.

**With 10 in-flight requests per worker** (`WASL_MAX_INFLIGHT_REQUESTS=10`):
* At 80 users, p95 of the served requests is 763 ms (goodput 171 per second) and 503s are fast (283 ms p95).
* The cost is shedding about a third of requests even at 40 users, because keep-alive connections spread unevenly
  across the SO_REUSEPORT workers.
* The default stays at 16, which sheds nothing up to 40 users.

**PostgreSQL 17 smoke test.** This used a small 300-patient database seeded with `wasl.seed`, so the absolute
figures are not comparable with the 10k SQLite runs.
* The validator found 0 violations and the audit chain stayed valid.
* At 40 users p95 was 356 ms (211 per second), with no errors.
* At 80 and 160 users the shape was the same as on SQLite. The remaining limit is Python CPU (4 workers on the
  same box as the load generator), not the database engine.

## Demo-server hang at 11:02 IST: root cause (fixed)

**Cause: locks shared across databases.** The cross-process **audit-writer lock**
(`data/.audit-writer.lock`) and the **SLA-sweeper election lock** (`data/.sla-sweeper.lock`) were keyed on
`data_dir`, not on the database. Every server and script using `./data` shared them, whatever database it served:
* the demo server on :8000;
* the load-test server on :8001, running on a copy in /tmp;
* the edge seed.

**Effect: the demo server waited on the load test.** Every request is audited, which is a write. While :8001 was
overloaded, the :8000 workers queued behind its lock holders on an unbounded `flock`, so audited requests hung.
A worker blocked there also ignored SIGTERM, which is why two old processes had to be killed with `kill -9`.

**Reproduced.** With :8001 at 80 users, 4 of 32 probes to :8000 timed out at 20 s. The :8001 workers also logged
"another worker owns the background SLA sweep": the demo server held the shared election lock, so **the load-test
server never ran its own SLA sweep**.

**After the fix.** Under the same 80–160-user load on :8001, the slowest :8000 probe was 0.11 s.

**The fix:**
* Lock files are now per database: `.audit-writer-<key>.lock` and `.sla-sweeper-<key>.lock`, where the key is a
  hash of the database file's real path. The same database file always maps to the same lock, so the hash chain
  cannot fork. Pre-fix servers were all stopped before the fixed code started.
* Both waits are bounded (`writer_lock_timeout_s`, 5 s), so overload gives 503 rather than an indefinite hang.

## Changes (round 21)

**Bounded waits.** Every wait has a limit:

| Setting | Value | Before |
|---|---|---|
| Pooled-connection wait (`db_pool_timeout_s`) | 3 s | 30 s |
| SQLite busy_timeout / Postgres lock_timeout (`db_busy_timeout_s`) | 5 s | 30 s |
| Writer-lock wait (`writer_lock_timeout_s`) | 5 s | none |

Timeouts, "database is locked" and saturation all answer **503 + Retry-After** ("nothing was saved").

**Admission control.** This is the outermost middleware: above `max_inflight_requests` (16 per worker), a request
gets an immediate 503 with no database work. Shed requests are counted and written to the audit log as an
aggregated `http.load_shed` event.

**Group-committed request log.**
* Each request's audit event is still committed **before** its response is sent.
* One writer thread per process commits the events of all concurrent requests in one transaction, so the
  cross-process writer lock is taken once per batch instead of once per request.
* The writer takes its pooled connection *before* the writer lock. Holding the lock while waiting for a
  connection was the cause of the uniform ~6 s stall at 80 users in the first round-21 run.
* **Governance tightened:** if the access log cannot be written, a read is refused (503) instead of being served
  unlogged. A committed write is still reported as done.

**Work-queue read-path cache** (base data, per process):
* at most 2 s old;
* dropped at once when this process commits any clinical change;
* RAG status, sorting and per-user org scope are still computed per request;
* every action still re-checks the case's row_version, so a stale view gets HTTP 409.

**Pool configuration, Postgres-ready:** `db_pool_size` 20 + `db_max_overflow` 20 per worker, `pool_recycle`;
on Postgres, `lock_timeout` and `idle_in_transaction_session_timeout`.

**Not changed:** no clinical logic changed. Alerts, rules and workflow are untouched.
