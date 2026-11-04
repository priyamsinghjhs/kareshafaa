# 2. Runnable project

Repository root: this `app/` directory. Python 3.12+ (CI uses 3.13). All data is synthetic.

## A. Local, SQLite (quickest)

```bash
git clone <repo-url> kareshafaa && cd kareshafaa
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m kareshafaa.seed --patients 2000 --seed 20261120     # fresh synthetic DB in data/
.venv/bin/python -m kareshafaa.serve --host 127.0.0.1 --port 8000 --workers 1
# open http://127.0.0.1:8000  - outside public mode, first passwords for the staff accounts are generated at random
# and written to the file named by KARESHAFAA_DEMO_CREDENTIALS_FILE (mode 0600, never committed)
```

Tests: `.venv/bin/python -m pytest -q` (builds its own throwaway database in a temp dir).

## B. Docker + PostgreSQL

```bash
cp .env.example .env   # or create .env with the three secrets below; never commit it
docker compose up --build    # app on http://localhost:8000, Postgres 16 in volume "pgdata"
```

`docker-compose.yml` reads the secrets `KARESHAFAA_SECRET_KEY`, `KARESHAFAA_DEMO_PASSWORD` and
`POSTGRES_PASSWORD` from `.env`. The container runs `python -m kareshafaa.bootstrap` (creates missing tables and
columns, additive only) before starting the server.

## C. One-command example (fresh database, fixed seed, run record)

```bash
bash scripts/handover_example.sh [OUT_DIR]     # default ./handover_run ; about 15 s after install
```

It creates `.venv` if missing, builds a new SQLite database with 300 synthetic patients (seed 2026), sends
`examples/input_oru_r01_fit_positive.hl7` and `examples/input_fhir_bundle_hba1c.json` through the real API,
records alert, tier, pathway target, SLA timer, the escalation sweep at +1 and +40 days, validates the FHIR
export and the audit chain, runs the rules engine against the independent reference on 20,000 randomised results
(seed 20262020) and writes `OUT_DIR/run_record.json` and `OUT_DIR/run_record.md`. The output committed in
`examples/example_run_record.*` came from this script. Verified from a fresh `git clone` in a temp dir
(see `test_results.md`). Times and case ids depend on the run date; decisions, tiers and checks do not.

## Environment variables

All settings use the prefix `KARESHAFAA_` (see `kareshafaa/config.py`); defaults suit local development.
**Secrets are referenced by name only – set them in `.env` or the hosting dashboard, never in git.**

| Variable | Secret? | Purpose |
|---|---|---|
| `KARESHAFAA_SECRET_KEY` | **secret** | Signing key for sessions and tokens (64 random characters) |
| `KARESHAFAA_DEMO_PASSWORD` | **secret** | First password for the provisioned staff accounts (internal variable name) |
| `DATABASE_URL` / `KARESHAFAA_DATABASE_URL` | **secret** when it holds a password | Database; default SQLite file in `data/` |
| `POSTGRES_PASSWORD` | **secret** | Docker Compose database password |
| `KARESHAFAA_MODE` | no | `standard`, `production` or `public_demo` (internal identifier) |
| `KARESHAFAA_ENV` | no | `dev`, `test`, `prod` |
| `KARESHAFAA_DEMO_LOGINS` | no | Role-switcher logins; **must be `false`** outside test environments |
| `KARESHAFAA_PUBLIC_DEMO` | no | Public sandbox with nightly reset (internal identifier) |
| `KARESHAFAA_DEMO_CREDENTIALS_FILE` | path only | Where generated first passwords are written (0600) |
| `KARESHAFAA_DATA_DIR`, `KARESHAFAA_MODEL_DIR` | no | Data and model folders |
| `KARESHAFAA_SWEEP_INTERVAL_SECONDS` | no | Background SLA/escalation sweep (300; 0 = off) |
| `KARESHAFAA_DATA_STEPS` | no | Start-up data steps: `background` or `off` |
| `KARESHAFAA_SESSION_IDLE_MINUTES`, `KARESHAFAA_TOKEN_TTL_MINUTES` | no | Session expiry |
| `KARESHAFAA_LOGIN_MAX_FAILURES`, `..._LOGIN_WINDOW_MINUTES`, `..._LOGIN_LOCKOUT_MINUTES` | no | Lockout |
| `KARESHAFAA_COOKIE_SECURE` | no | Force the Secure cookie flag (auto by default) |
| `KARESHAFAA_SUPPRESSION_THRESHOLD` | no | Small-cell suppression in reports (5) |
| `KARESHAFAA_MAX_INFLIGHT_REQUESTS`, `KARESHAFAA_DB_POOL_SIZE` | no | Load shedding and pool size |
| `KARESHAFAA_SLA_CONFIG_PATH` | no | Optional JSON overriding default SLA hours |
| `WEB_WORKERS` | no | Server worker processes |
| `KARESHAFAA_BOOTSTRAP_SOURCE` | no | SQLite file copied into an empty Postgres on first start |

Other tuning values (ML gates, fairness tolerances, escalation fractions) are listed with comments in
`kareshafaa/config.py`.
