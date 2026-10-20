# Wasl - Closing the Screening-to-Care Loop (prototype, synthetic data only)
PY      ?= python3
VENV    ?= .venv
BIN      = $(VENV)/bin
HOST    ?= 0.0.0.0
PORT    ?= 8000
WORKERS ?= 4
PATIENTS ?= 2000

.PHONY: help setup seed seed-large validate demo run dev test test-clinical check ui-smoke clean

help:
	@echo "make setup   - create virtualenv and install pinned-compatible deps"
	@echo "make seed    - (re)build the synthetic database, train + register models (~1 min)"
	@echo "make seed-large - 20,000-patient realistic cohort (population rates, defects) into data/wasl_large.db"
	@echo "make validate   - check every record in the database against the clinical/data invariants"
	@echo "make run     - start the web app on http://localhost:$(PORT)"
	@echo "make test    - run the pytest suite (unit + API integration)"
	@echo "make demo    - seed then run"

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip -q
	$(BIN)/pip install -q -r requirements-dev.txt

seed:
	$(BIN)/python -m wasl.seed --patients $(PATIENTS)

LARGE_DB ?= sqlite:///./data/wasl_large.db
seed-large:
	WASL_DATABASE_URL=$(LARGE_DB) WASL_MODEL_DIR=./data/models_large $(BIN)/python -m wasl.seed --patients 20000 --realistic --no-train

validate:
	$(BIN)/python scripts/validate_data.py

validate-large:
	WASL_DATABASE_URL=$(LARGE_DB) $(BIN)/python scripts/validate_data.py

demo: seed run

run:
	$(BIN)/uvicorn wasl.main:app --host $(HOST) --port $(PORT) --workers $(WORKERS)

dev:
	$(BIN)/uvicorn wasl.main:app --host $(HOST) --port $(PORT) --reload

test:
	$(BIN)/python -m pytest

check: test

ui-smoke:  ## optional: needs `pip install playwright` and a local Chrome; server must be running
	$(BIN)/python scripts/ui_smoke.py http://localhost:$(PORT)

clean:
	rm -rf data/wasl.db* data/models/*.joblib .pytest_cache
