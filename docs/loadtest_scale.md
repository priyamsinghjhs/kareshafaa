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

# After: work queue in SQL + batched proposal lookups (5 Nov 2026, branch `perf`)

Same seeded databases (copies of the baseline ones, upgraded by the start-up migration: two additive columns, two
partial indexes, backfill of 20,000 open cases in < 1 s), same box and settings. Cells: **before → after**.
Produced by `scripts/loadtest/compare.py /tmp/lt/baseline /tmp/lt-after 500 5000 20000`.

| Scale (open cases) | 500 | 5000 | 20000 |
|---|---|---|---|
| rows: patients / cases / steps / comms | 1363 / 1363 / 9428 / 3226 | 10000 / 10000 / 65933 / 25019 | 40000 / 40000 / 263689 / 100051 |
| rows: questionnaire responses / lessons | 1277 / 1584 | 9082 / 11150 | 36305 / 44513 |
| work_queue() service, median ms / SQL / peak MB | 11.6 / 11 / 1.6 → **6.6 / 4 / 0.4** | 81.9 / 11 / 12.1 → **24.5 / 4 / 0.4** | 403.3 / 11 / 47.0 → **117.6 / 4 / 0.4** |
| queue 1 user, p50 / p95 ms (cache off) | 19.5 / 37.3 → **13.1 / 15.5** | 96.1 / 159.4 → **32.9 / 36.8** | 427.3 / 478.5 → **125.4 / 142.5** |
| queue 10 users, p50 / p95 ms, ok/total (cache off) | 260.3 / 330.7 (60/60) → **115.9 / 133.0 (60/60)** | 1746.4 / 9382.2 (59/60) → **143.6 / 173.8 (60/60)** | 9799.4 / 10994.8 (60/60) → **582.6 / 640.5 (60/60)** |
| queue 25 users, p50 / p95 ms, ok/total (cache off) | 411.8 / 495.1 (96/150) → **170.4 / 256.0 (90/150)** | 4266.1 / 4620.6 (96/150) → **213.4 / 242.5 (89/150)** | 15429.0 / 16317.5 (96/150) → **1007.2 / 1261.5 (96/150)** |
| queue 1 user, p50 / p95 ms (2 s cache (default)) | 10.6 / 13.6 → **13.1 / 14.6** | 32.9 / 51.5 → **33.1 / 34.9** | 97.1 / 121.8 → **120.6 / 139.4** |
| queue 10 users, p50 / p95 ms, ok/total (2 s cache (default)) | 104.6 / 141.8 (60/60) → **115.9 / 194.7 (60/60)** | 411.7 / 580.4 (60/60) → **149.2 / 170.4 (60/60)** | 1600.2 / 6091.3 (59/60) → **579.7 / 641.8 (60/60)** |
| queue 25 users, p50 / p95 ms, ok/total (2 s cache (default)) | 164.2 / 243.7 (96/150) → **177.4 / 232.9 (92/150)** | 609.8 / 710.0 (96/150) → **233.0 / 301.7 (91/150)** | 4909.5 / 6662.4 (96/150) → **1082.0 / 1793.3 (96/150)** |
| GET /api/queue?view=review (GP): p50 / p95 ms / SQL | 16.8 / 20.3 / 16 → **8.9 / 10.7 / 6** | 75.5 / 151.7 / 16 → **17.6 / 19.9 / 6** | 346.6 / 8664.8 / 16 → **60.4 / 63.7 / 6** |
| GET /api/cases/{id}: p50 / p95 ms / SQL | 14.2 / 18.9 / 34 → **15.7 / 21.1 / 34** | 13.7 / 16.2 / 32 → **15.8 / 24.7 / 32** | 16.2 / 84.8 / 32 → **14.7 / 17.2 / 32** |
| GET /api/analytics/kpis (dashboard): p50 / p95 ms / SQL | 5.1 / 6.4 / 5 → **4.1 / 4.9 / 5** | 5.5 / 6.6 / 5 → **5.2 / 7.7 / 5** | 5.5 / 8.7 / 5 → **5.0 / 6.3 / 5** |
| GET /api/analytics/impact (dashboard): p50 / p95 ms / SQL | 5.9 / 6.9 / 5 → **5.3 / 7.3 / 5** | 5.8 / 7.6 / 5 → **6.1 / 7.6 / 5** | 5.7 / 10.5 / 5 → **5.4 / 6.8 / 5** |
| GET /api/patients?q=SYN-: p50 / p95 ms / SQL | 14.4 / 17.4 / 8 → **13.7 / 16.2 / 8** | 18.1 / 24.4 / 8 → **18.7 / 20.1 / 8** | 26.7 / 33.2 / 8 → **27.4 / 33.1 / 8** |
| GET /api/recalls?view=upcoming: p50 / p95 ms / SQL | 21.3 / 26.3 / 7 → **23.8 / 31.6 / 7** | 58.4 / 125.5 / 7 → **60.7 / 145.4 / 7** | 69.0 / 360.0 / 7 → **71.8 / 141.2 / 7** |
| GET /queue (HTML): p50 / p95 ms / SQL | 7.0 / 7.9 / 7 → **6.9 / 11.5 / 7** | 6.9 / 7.9 / 7 → **6.9 / 7.7 / 7** | 6.6 / 7.3 / 7 → **6.7 / 7.7 / 7** |
| GET /cases/{id} (HTML): p50 / p95 ms / SQL | 5.7 / 8.2 / 7 → **6.0 / 7.2 / 7** | 6.2 / 6.8 / 7 → **6.4 / 7.0 / 7** | 6.0 / 8.3 / 7 → **6.1 / 6.6 / 7** |
| GET /dashboard (HTML): p50 / p95 ms / SQL | 5.4 / 6.0 / 7 → **6.2 / 6.9 / 7** | 6.1 / 13.4 / 7 → **6.4 / 7.2 / 7** | 6.1 / 6.5 / 7 → **6.3 / 6.8 / 7** |
| dashboard kpis() without aggregate cache: ms / SQL | 72.5 / 13 → **76.2 / 13** | 526.8 / 13 → **559.5 / 13** | 1899.3 / 13 → **2096.1 / 13** |
| dashboard impact() without aggregate cache: ms / SQL | 75.4 / 13 → **77.7 / 13** | 499.1 / 13 → **522.7 / 13** | 1781.5 / 13 → **1927.6 / 13** |
| GET /api/queue (coordinator, page 50): SQL statements (endpoint) | 16 → **9** | 16 → **9** | 16 → **9** |
| generate_proposals(): median / max ms | 41.8 / 50.4 → **34.2 / 40.6** | 187.7 / 283.1 → **179.2 / 272.4** | 690.9 / 717.8 → **679.2 / 876.6** |
| generate_proposals(): SQL / proposals | 86 / 20 → **51 / 20** | 144 / 39 → **89 / 39** | 150 / 41 → **93 / 41** |
| generate_proposals(): peak MB (tracemalloc / RSS delta) | 3.9 / 6.4 → **3.9 / 7.2** | 26.4 / 25.2 → **26.4 / 32.3** | 104.9 / 145.0 → **104.9 / 161.7** |

## What changed

* **Work queue.** Each open case stores the absolute times its in-progress step turns amber (`sla_amber_at`) and red
  (`sla_red_at`) – computed with the same `rag_status` function, exact to the microsecond. The queue compares them
  with the current time in SQL, so the colour is never stale, and does filters, per-view counts, ordering and paging
  in SQL (one aggregate query, one narrow sort-key query for the page's ids, one detail query for those rows only).
  Kept current by a flush hook on every Case / StepTask / SlaRule change, by the sample-date data step (which moves
  times with bulk updates), and recomputed idempotently at every start-up. Equivalence with the former Python path:
  0 mismatches in 1,900 comparisons on the test database, the 5,000 database and a local PostgreSQL 16 database (every
  queue user × 10 views × 5 'now' times) and 400 on the 20,000 database; exact ties (same status, tier, review flag,
  rounded score and due time) are now ordered by case id instead of the database's scan order.
* **Proposals.** Pending-proposal fingerprints, the active configuration (channel order, template variants) and
  referral organisations are read once per run; proposals are flushed once. Same proposals (checked at all three
  scales); SQL statements −38 to −41 %. No time window was added, so runtime and memory still grow with history.
* **Not changed: dashboard aggregates** (`kpis()` / `impact_report()` ~1.9 s uncached at 20,000). They build
  medians and rates from every case and step in pandas; the stored thresholds do not help, and making them cheap means
  moving those aggregates into SQL – a separate change. They stay behind the existing 60 s aggregate cache.
* The queue no longer uses the 2 s queue cache (`queue_cache_ttl_s`); the "2 s cache" rows therefore measure the same
  code path as "cache off". At 25 users the per-worker in-flight limit (16) still refuses ~40 % of requests at once.
* Remaining cost at 20,000: ~120 ms per queue request (counts + sort over all matching open cases in SQL). Further
  gains would need fewer per-request counts or a stored sort rank per status band.
