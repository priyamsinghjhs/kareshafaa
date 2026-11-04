# Background jobs

KareShafaa has no separate queue service. Background work runs inside the web process, so the free hosting plan
(one small instance) needs nothing extra. Each job is idempotent: it can run again after a crash or on another
server without doing the work twice.

| Job | When | What it does | Safe to repeat because |
|---|---|---|---|
| Deadline check (SLA sweep) | every `KARESHAFAA_SWEEP_INTERVAL_SECONDS` (300 s on hosting) | Marks step clocks amber/red, raises L1-L4 escalations, re-checks recall due dates | Each case stores the highest escalation level already raised, so a re-run raises only new levels; a concurrent edit makes the sweep re-run on fresh data; a clock that moves backwards skips the run |
| Retention purge | with the deadline check, at most once an hour | Removes sign-in attempts older than 90 days and ended sessions older than 30 days | Deletes only rows past the cut-off; records `retention.purged` in the audit log |
| Audit writer | continuously | Appends request audit rows to the hash chain in small batches | Each row carries the previous row's hash; the chain check finds any gap |
| Audit fallback reconciliation | with the deadline check (and **Admin → System → Add them to the audit trail now**) | Appends request audit events that could not be written at the time (kept in `audit_fallback_events` or `DATA_DIR/audit-fallback.jsonl`) to the hash chain with their original timestamps, marked `reconciled_late` | Each kept event has a unique `fallback_id`; appending it and marking it reconciled happen in one transaction, and file lines already imported are skipped |
| One-time data steps | after start-up | Small additive updates to sample (SYN-) records, in small batches in a worker thread | Never blocks serving; never resets data |

Only one worker per server and database runs the timed jobs (a non-blocking file lock elects it), so `WEB_WORKERS > 1` does not double them.

## Moving to more than one server

When the service runs on several servers (for example two or more containers behind a load balancer):

1. Run the timed jobs in **one** place: either set `KARESHAFAA_SWEEP_INTERVAL_SECONDS=0` on the web servers and
   call `POST /api/sla/sweep` (permission `sweep:run`) from a scheduler (Kubernetes CronJob, systemd timer) with a service account, or keep one server with the
   sweep switched on.
2. If the sweep must run on several servers, use a PostgreSQL advisory lock around it so only one runs at a time.
   (Design only; not built, because the free plan has one server.)
3. Patient messages: in a real deployment, sending moves to a queue (for example PostgreSQL `LISTEN/NOTIFY` or a
   managed queue in a UAE region) with retries and delivery receipts. The prototype simulates delivery and
   records each attempt as a Communication.

## Monitoring

* `/healthz` for liveness, `/readyz` for readiness (returns 503 until the database answers and initial records exist).
* `KARESHAFAA_LOG_FORMAT=json` writes one JSON object per log line (`ts`, `level`, `logger`, `msg`) for a log collector.
* Each sweep logs its result and duration; Admin → System shows the data store and its persistence, and an alert
  with the number of audit events waiting to be reconciled (admin role only).
