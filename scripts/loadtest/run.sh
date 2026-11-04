#!/usr/bin/env bash
# Baseline / after load test on throw-away local SQLite databases. Usage (from the repo root):
#   bash scripts/loadtest/run.sh [ROOT=/tmp/lt] [SCALES="500 5000 20000"] [PORT=8091]
# Seeds each scale once (kept in $ROOT/<scale>; delete the dir to reseed), runs the in-process measurements,
# then the HTTP test twice: with the default 2 s queue cache and with the cache off. Summary: $ROOT/summary.md
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=${ROOT:-/tmp/lt}
SCALES=${SCALES:-"500 5000 20000"}
PORT=${PORT:-8091}
PY=.venv/bin/python
unset DATABASE_URL KARESHAFAA_DATABASE_URL || true

serve() {  # $1 data dir, $2 queue cache ttl
  KARESHAFAA_DATA_DIR="$1" KARESHAFAA_DATABASE_URL="sqlite:///$1/kareshafaa.db" KARESHAFAA_MODEL_DIR="$1/models" \
  KARESHAFAA_DEMO_CREDENTIALS_FILE="$1/credentials.txt" KARESHAFAA_SWEEP_INTERVAL_SECONDS=0 \
  KARESHAFAA_QUEUE_CACHE_TTL_S="$2" .venv/bin/uvicorn kareshafaa.main:app --host 127.0.0.1 --port "$PORT" \
    --log-level warning >"$1/server_ttl$2.log" 2>&1 &
  SERVER=$!
  for _ in $(seq 1 120); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && return 0; sleep 0.5; done
  echo "server did not start; see $1/server_ttl$2.log" >&2; kill "$SERVER"; exit 1
}

for n in $SCALES; do
  d="$ROOT/$n"
  if [ ! -f "$d/kareshafaa.db" ]; then
    $PY scripts/loadtest/seed_scale.py --open "$n" --data-dir "$d"
  fi
  $PY scripts/loadtest/measure_inprocess.py --data-dir "$d" 2>/dev/null
  for ttl in 2 0; do
    serve "$d" "$ttl"
    $PY scripts/loadtest/http_load.py --base "http://127.0.0.1:$PORT" --data-dir "$d" --label "ttl$ttl" || true
    kill "$SERVER"; wait "$SERVER" 2>/dev/null || true
  done
done
$PY scripts/loadtest/summarize.py "$ROOT" $SCALES | tee "$ROOT/summary.md"
