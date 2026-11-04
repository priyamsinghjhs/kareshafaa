#!/usr/bin/env bash
# CI accessibility run: seed a THROWAWAY synthetic database in a temp dir, start the app on it, run the axe-core
# WCAG 2.1 AA + keyboard-focus check (scripts/a11y_check.py), stop the app. Never touches data/kareshafaa.db.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
PORT=${PORT:-8058}
D=$(mktemp -d)
trap 'kill ${SRV:-0} 2>/dev/null || true; rm -rf "$D"' EXIT
export KARESHAFAA_ENV=dev KARESHAFAA_DATABASE_URL="sqlite:///$D/ci.db" KARESHAFAA_DATA_DIR="$D" \
       KARESHAFAA_MODEL_DIR="$D/models" KARESHAFAA_SECRET_KEY="$(openssl rand -hex 32)" \
       KARESHAFAA_DEMO_CREDENTIALS_FILE="$D/creds.txt" KARESHAFAA_DATA_STEPS=off
$PY -m kareshafaa.seed --patients ${PATIENTS:-300} --seed 1234 --no-sim > "$D/seed.log"
$PY -m kareshafaa.serve --host 127.0.0.1 --port "$PORT" --workers 1 > "$D/srv.log" 2>&1 &
SRV=$!
for _ in $(seq 1 120); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/healthz" && break; sleep 0.5; done
$PY scripts/a11y_check.py "http://127.0.0.1:$PORT" "${1:-$D/a11y.json}"
