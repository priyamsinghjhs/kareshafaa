# Scale load test (local SQLite)

Reproducible load test of the work queue, proposal generation and the main pages at several data sizes. Everything
runs on throw-away SQLite databases in a temp directory (the scripts refuse a data dir inside the repository and
ignore any `DATABASE_URL` in the shell). Results: [`docs/loadtest_scale.md`](../../docs/loadtest_scale.md).

```bash
bash scripts/loadtest/run.sh                                   # all scales: 500, 5,000, 20,000 open cases
ROOT=/tmp/lt SCALES="500 5000" PORT=8091 bash scripts/loadtest/run.sh   # subset / other dir / port
```

Per scale (`$ROOT/<open cases>/`):

| Step | Script | Output |
|---|---|---|
| Seed once (≈35 s at 500, ≈50 s at 20,000; delete the dir to reseed) | `seed_scale.py --open N --data-dir DIR` | `seed_summary.json` |
| SQL statements per endpoint, `work_queue()`, `generate_proposals()` runtime + peak memory, dashboard aggregates without cache | `measure_inprocess.py --data-dir DIR` | `inprocess.json` |
| HTTP latency (1 uvicorn worker, SLA sweep off): sequential p50/p95 per endpoint, queue page with 10 and 25 users | `http_load.py --data-dir DIR --label L` | `http_ttl2.json` (default 2 s queue cache), `http_ttl0.json` (cache off) |
| Table across scales | `summarize.py ROOT 500 5000 20000` | `$ROOT/summary.md` |

Data: the app's own seeder (`kareshafaa.seed`, fixed seed 7, 1,000 synthetic patients replayed through the normal
services) builds the base; `seed_scale.py` then copies whole synthetic patient histories (cases, steps,
communications, questionnaire responses, lessons, recalls, ...) through the model tables until the target number of
open cases is reached, with about one closed-only patient per patient with an open case. All MRNs are `SYN-`.

Sign-in uses the normal password login (login-page CSRF token + `POST /api/auth/login`) with the random local
passwords the app writes to `credentials.txt` in the data dir at start-up (mode 0600; never printed).

For "after" numbers, re-run the same command on the changed code; the seeded databases are reused, so before and
after see identical data. Compare `$ROOT/summary.md`.
