# Background jobs

KareShafaa has no separate queue service. Background work runs inside the web process, so the free hosting plan
(one small instance) needs nothing extra. Each job is idempotent: it can run again after a crash or on another
server without doing the work twice.

| Job | When | What it does | Safe to repeat because |
|---|---|---|---|
| Deadline check (SLA sweep) | every `KARESHAFAA_SWEEP_INTERVAL_SECONDS` (300 s on hosting) | Marks step clocks amber/red, raises L1-L4 escalations, re-checks recall due dates. Evaluates every open case read-only first, then writes only the cases that change (the audit writer lock is held only for that part) | Each case stores the highest escalation level already raised, so a re-run raises only new levels; a concurrent edit makes the sweep re-run on fresh data; a clock that moves backwards skips the run |
| Retention purge | with the deadline check, at most once an hour | Removes sign-in attempts older than 90 days and ended sessions older than 30 days | Deletes only rows past the cut-off; records `retention.purged` in the audit log |
| Audit writer | continuously | Appends request audit rows to the hash chain in small batches | Each row carries the previous row's hash; the chain check finds any gap |
| Audit fallback reconciliation | with the deadline check (and **Admin → System → Add them to the audit trail now**) | Appends request audit events that could not be written at the time (kept in `audit_fallback_events` or `DATA_DIR/audit-fallback.jsonl`) to the hash chain with their original timestamps, marked `reconciled_late` | Each kept event has a unique `fallback_id`; appending it and marking it reconciled happen in one transaction, and file lines already imported are skipped |
| Result-to-case reconciliation | with the deadline check (and **Admin → System → Check results now**, **Results inbox → Check now**) | Lists screening results that need action but have no case, and processed lab messages that filed no result, in `result_reconciliation_items` (owner: care coordination) | One open item per source row; re-runs only update `last_seen_at`; an item whose result now has a case is closed as `linked_later` |
| One-time data steps | after start-up | Small additive updates to sample (SYN-) records, in small batches in a worker thread | Never blocks serving; never resets data |

Only **one process** runs the timed jobs (deadline check, retention purge, audit-fallback reconciliation),
however many workers and containers there are (`kareshafaa/worker_lock.py`):

* **PostgreSQL:** a session-level advisory lock, `pg_try_advisory_lock(727001)`, taken on a dedicated connection
  (outside the request pool, autocommit, TCP keepalives, `application_name=kareshafaa-timed-jobs`) and held for the
  life of the worker. Before every run the holder re-checks in `pg_locks` that its own session still holds it; if
  the connection was lost (database restart, network cut, Neon compute restart) the database has already released
  the lock, so the process stops running jobs and tries to take it again on a new connection. Every other process
  retries once per interval, so if the holder dies another container takes over within one interval. At shutdown
  the lock is released (`pg_advisory_unlock`, connection closed); a killed process loses it when its session ends.
* **SQLite:** a non-blocking `flock` on `DATA_DIR/.sla-sweeper-<database key>.lock`, held for the life of the
  process (only processes on one host can share a SQLite file). Losers retry each interval as above.

**Pooled connections do not work for this lock.** Session-level advisory locks belong to one database session;
behind a transaction-mode pooler (PgBouncer, Neon's `-pooler` host, Supabase port 6543) consecutive statements can
run on different server sessions, so the lock could be taken on one session and "checked" on another. The lock
connection therefore uses the direct host: a Neon `ep-…-pooler.…neon.tech` host is mapped to `ep-….neon.tech`
automatically; for any other pooler set `KARESHAFAA_WORKER_LOCK_DATABASE_URL` to the direct URL (the app logs a
warning when the URL looks pooled). The audit writer's per-transaction lock (`pg_advisory_xact_lock`) is unaffected.

## Moving to more than one server

When the service runs on several servers (for example two or more containers behind a load balancer):

1. Run the timed jobs in **one** place: either set `KARESHAFAA_SWEEP_INTERVAL_SECONDS=0` on the web servers and
   call `POST /api/sla/sweep` (permission `sweep:run`) from a scheduler (Kubernetes CronJob, systemd timer) with a service account, or keep one server with the
   sweep switched on.
2. Or simply leave the sweep switched on everywhere: on PostgreSQL the advisory lock above lets only one container
   run the timed jobs at a time and hands over automatically if it stops. Use the direct (unpooled) URL for it.
3. Patient messages: in a real deployment, sending moves to a queue (for example PostgreSQL `LISTEN/NOTIFY` or a
   managed queue in a UAE region) with retries and delivery receipts. The prototype simulates delivery and
   records each attempt as a Communication.

## Monitoring

* `/healthz` for liveness, `/readyz` for readiness (returns 503 until the database answers and initial records exist).
* `KARESHAFAA_LOG_FORMAT=json` writes one JSON object per log line (`ts`, `level`, `logger`, `msg`) for a log collector.
* Every timed job records its last start, finish, success, failure (exception class name only), duration, run and
  failure counts and a small count summary in `background_job_status` (`kareshafaa/services/job_health.py`); the
  **Monitoring** card on Admin → System shows them with the results-without-a-case count, results waiting for a
  patient match and open escalations. Free: no external APM.
* Each sweep logs its result and duration; Admin → System shows the data store and its persistence, and an alert
  with the number of audit events waiting to be reconciled (admin role only).
