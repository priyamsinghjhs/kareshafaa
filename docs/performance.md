# Performance (round 2, 4 Nov 2026)

Measured locally on the box; not on the hosted site. Scripts: `scripts/profile_queries.py` (SQL statements per
endpoint, in-process), `scripts/perf_check.py` (Playwright + Chrome, mobile 412×823, 4× CPU slowdown, simulated
slow 4G 150 ms RTT / 1.6 Mbit/s, median of 3 runs; "cold" = empty cache, "warm" = repeat visit).

## Database queries (PostgreSQL 16.2, production-mode app, same synthetic data)

| Endpoint | Before | After | Fix |
|---|---|---|---|
| `GET /api/recalls?view=upcoming` | 206 statements / 72 ms | **7 / 31 ms** | batch visibility check (`visible_patient_ids`), organisations preloaded |
| `GET /api/patients?q=…` | 66 / 28 ms | **8 / 17 ms** | batch visibility, organisations prefetched, open cases counted in one GROUP BY |
| `GET /api/cases/{id}` | 51 statements | **33** | practitioners and staff users preloaded |

11 indexes added on foreign keys used by these paths (additive; `init_db` creates missing indexes on existing
databases, checked on a copy). Lists stay capped server-side (recalls 200, queue paged).

## Page weight and paint (throttled mobile)

| Page | Transfer KB cold (before → after) | FCP ms cold | LCP ms cold | Transfer KB warm | LCP ms warm |
|---|---|---|---|---|---|
| login | 99.8 → **33.8** | 840 → **560** | 840 → **560** | 11.9 → **3.9** | 356 → **212** |
| coordinator queue | 139.9 → **44.1** | 812 → **544** | 1144 → **876** | 51.9 → **14.1** | 808 → **540** |
| case | 234.2 → **61.2** | 1004 → **568** | 1944 → **1136** | 93.7 → **15.2** | 1284 → **644** |
| lead outcomes | 120.3 → **41.8** | 876 → **536** | 1328 → **1004** | 16.8 → **7.0** | 600 → **592** |
| patient app | 161.8 → **54.2** | 968 → **556** | 1332 → **912** | 40.7 → **15.7** | 624 → **568** |

Fixes: gzip for responses ≥ 1 KB; static files served with versioned URLs (`?v=<sha256>`) and
`Cache-Control: public, max-age=31536000, immutable` (unversioned: 5 minutes); non-critical scripts (`pwa.js`,
`tour.js`) deferred. Brotli was **not** added (Starlette ships gzip only). TTFB stays 5–9 ms locally.

## Start-up

Container-style chain (bootstrap → server → `/healthz` 200), local: **3.4 s → 2.4 s**. App import 1.5 s →
0.88 s, because scikit-learn now loads on first model use instead of at import. A cached `/starting` page is shown
by the service worker when a navigation takes more than 4 s (CONFIRM value) – this helps returning visitors only;
the free host's own proxy still holds a first-ever request while the instance wakes.

Raw results: `dist/perf/before_throttled.json`, `dist/perf/after2_throttled.json` (outside the repository).
