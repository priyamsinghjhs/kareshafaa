# Wasl - Closing the Screening-to-Care Loop (prototype, synthetic data only)
PY      ?= python3
VENV    ?= .venv
BIN      = $(VENV)/bin
HOST    ?= 0.0.0.0
PORT    ?= 8000
PATIENTS ?= 2000

.PHONY: help setup seed demo run dev test check ui-smoke clean

help:
	@echo "make setup   - create virtualenv and install pinned-compatible deps"
	@echo "make seed    - (re)build the synthetic database, train + register models (~1 min)"
	@echo "make run     - start the web app on http://localhost:$(PORT)"
	@echo "make test    - run the pytest suite (unit + API integration)"
	@echo "make demo    - seed then run"

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip -q
	$(BIN)/pip install -q -r requirements-dev.txt

seed:
	$(BIN)/python -m wasl.seed --patients $(PATIENTS)

demo: seed run

run:
	$(BIN)/uvicorn wasl.main:app --host $(HOST) --port $(PORT)

dev:
	$(BIN)/uvicorn wasl.main:app --host $(HOST) --port $(PORT) --reload

test:
	$(BIN)/python -m pytest

check: test

ui-smoke:  ## optional: needs `pip install playwright` and a local Chrome; server must be running
	$(BIN)/python scripts/ui_smoke.py http://localhost:$(PORT)

clean:
	rm -rf data/wasl.db* data/models/*.joblib .pytest_cache
