#!/usr/bin/env bash
# One command, from a clean clone: builds a fresh local SQLite database with synthetic data, sends the sample
# HL7 and FHIR inputs through the API, runs a fixed-seed evaluation and writes the run record.
#   bash scripts/handover_example.sh [OUT_DIR]     (default: ./handover_run)
# Creates .venv on first use. Never touches data/kareshafaa.db.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:-handover_run}
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt "httpx>=0.27" fhir.resources
fi
.venv/bin/python -c "import fhir.resources, httpx" 2>/dev/null || .venv/bin/pip install -q "httpx>=0.27" fhir.resources
.venv/bin/python scripts/handover_example.py "$OUT"
echo "Run record: $OUT/run_record.md and $OUT/run_record.json"
