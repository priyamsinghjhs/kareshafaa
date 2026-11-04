# Scale load test – baseline (5 Nov 2026, commit fda8845 code, no performance fixes)

Local box (8 vCPU Xeon, Python 3.13.5, SQLite 3.46.1, SQLAlchemy 2.1.6), **one uvicorn worker**, SLA sweep off,
production defaults otherwise (`max_inflight_requests` 16, 2 s queue cache, 60 s aggregate cache). How to run:
[`scripts/loadtest/README.md`](../scripts/loadtest/README.md). Latency = client-side wall time over HTTP on
127.0.0.1; "cache off" = `KARESHAFAA_QUEUE_CACHE_TTL_S=0` (every request does the full queue work, as after any
write). Sequential: 30 requests after one warm-up. Concurrent: N users × 6 requests to the coordinator queue page.

| Scale (open cases) | 500 | 5,000 | 20,000 | growth 500→20k |
|---|---|---|---|---|
| rows: patients / cases / steps / communications | 1,363 / 1,363 / 9,428 / 3,226 | 10,000 / 10,000 / 65,933 / 25,019 | 40,000 / 40,000 / 263,689 / 100,051 | |
| rows: questionnaire responses / lessons | 1,277 / 1,584 | 9,082 / 11,150 | 36,305 / 44,513 | |
| `work_queue()` service: median ms / SQL / peak MB | 11.6 / 11 / 1.6 | 81.9 / 11 / 12.1 | 403 / 11 / 47.0 | ×35 time, ×29 memory |
| queue, 1 user, p50 / p95 ms (cache off) | 19.5 / 37.3 | 96.1 / 159 | 427 / 479 | ×22 |
| queue, 10 users, p50 / p95 ms (cache off) | 260 / 331 | 1,746 / 9,382 ¹ | 9,799 / 10,995 | ×38 |
| queue, 25 users, p50 / p95 ms, ok/total (cache off) | 412 / 495 (96/150) ² | 4,266 / 4,621 (96/150) | 15,429 / 16,318 (96/150) | ×37 |
| queue, 1 user, p50 / p95 ms (2 s cache) | 10.6 / 13.6 | 32.9 / 51.5 | 97.1 / 122 | ×9 |
| queue, 10 users, p50 / p95 ms (2 s cache) | 105 / 142 | 412 / 580 | 1,600 / 6,091 ¹ | ×15 |
| queue, 25 users, p50 / p95 ms, ok/total (2 s cache) | 164 / 244 (96/150) ² | 610 / 710 (96/150) | 4,910 / 6,662 (96/150) | ×30 |
| queue endpoint SQL statements | 16 | 16 | 16 | constant |
| GP queue (`view=review`), p50 / p95 ms (cache off) | 16.8 / 20.3 | 75.5 / 152 | 347 / 8,665 ³ | ×21 |
| `GET /api/cases/{id}` p50 / p95 ms / SQL | 14.2 / 18.9 / 34 | 13.7 / 16.2 / 34 | 16.2 / 84.8 / 34 | flat |
| `GET /api/patients?q=SYN-` p50 / p95 ms / SQL | 14.4 / 17.4 / 8 | 18.1 / 24.4 / 8 | 26.7 / 33.2 / 8 | ×1.9 |
| `GET /api/recalls?view=upcoming` p50 / p95 ms / SQL | 21.3 / 26.3 / 7 | 58.4 / 126 / 7 | 69.0 / 360 / 7 | ×3.2 |
| dashboard `GET /api/analytics/kpis` (cached) p50 / p95 ms / SQL | 5.1 / 6.4 / 5 | 5.5 / 6.6 / 5 | 5.5 / 8.7 / 5 | flat |
| dashboard `kpis()` without aggregate cache, ms / SQL | 72.5 / 13 | 527 / 13 | 1,899 / 13 | ×26 |
| dashboard `impact_report()` without aggregate cache, ms / SQL | 75.4 / 13 | 499 / 13 | 1,782 / 13 | ×24 |
| HTML shells `/queue`, `/cases/{id}`, `/dashboard` p50 ms / SQL | 5–7 / 7 | 6–7 / 7 | 6–7 / 7 | flat |
| `generate_proposals()` median / max ms | 41.8 / 50.4 | 188 / 283 | 691 / 718 | ×17 |
| `generate_proposals()` SQL statements / proposals | 86 / 20 | 144 / 39 | 150 / 41 | ×1.7 |
| `generate_proposals()` peak MB (tracemalloc / RSS increase) | 3.9 / 6.4 | 26.4 / 25.2 | 105 / 145 | ×27 / ×23 |

¹ One slow outlier batch (or one 503) inflates p95 at 10 users. ² At 25 users, 54 of 150 requests at every scale
are refused at once with 503 + Retry-After by the per-worker in-flight limit (16); p50/p95 are over the 96 served.
³ 2 of 30 sequential GP-queue requests at 20,000 returned 503 "service busy" after ~8.6 s (request-audit writer lock
wait > 5 s); worth a look on its own.

## Reading

* **Worst growth: the work queue.** Its SQL count is constant (16), but it loads every open case, computes SLA/RAG in
  Python and sorts before taking the page, so time and memory grow linearly with open cases (service ×35, 10 users
  ×38). At 20,000 open cases 10 users wait ~10 s at p50 and 25 users ~15 s – about half the 30 s request budget –
  on an 8-core box. The 2 s cache cuts single-user time ×4 but not the O(open cases) work per request.
* **Proposal generation** grows ×17 in time and ×23–27 in memory (whole tables loaded into Python); its statement
  count grows only ×1.7 (one lookup + insert per candidate, ~40 candidates). It is an admin job, not a page.
* **Dashboard aggregates** are cheap while cached but cost ~1.9 s each at 20,000 when the data fingerprint changes
  (recomputed at most every 10 s per process under writes).
* Case page, patient search and HTML shells stay flat; recalls grows mildly.

## Caveats

* Local SQLite numbers. **Render free tier + Neon Postgres will differ**: the free instance has a fraction of one
  CPU and 512 MB RAM (~220 MB RSS idle), so CPU-bound Python work such as the queue sort and the proposal
  aggregation will be several times slower; every SQL statement is a network round trip to Neon (statement counts
  matter more – e.g. ~150 per proposal run), and Neon's free compute suspends when idle (first query after idle
  waits for it to resume). Loading all open cases also means transferring every row over the network.
* Memory: `generate_proposals()` added ~145 MB RSS at 20,000 open cases; on a 512 MB instance with one worker that
  is a real out-of-memory risk.
* The synthetic history spans at most ~300 days, so a 90-day window would keep 54–69 % of the rows today (completed
  steps 54 %, communications 69 %, questionnaire responses 58 %, lessons 59 %); its benefit grows as real history
  accumulates.
* Scaled data are copies of 1,000 replayed patient histories (same distribution of states, SLA status and dates),
  bulk-loaded without audit events; no outreach-priority model is trained, so risk scores are empty.
