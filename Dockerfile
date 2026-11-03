# KareShafaa image. Build: docker build -t kareshafaa . ; run: docker run -p 8000:8000 -e KARESHAFAA_MODE=production \
#   -e KARESHAFAA_SECRET_KEY=... -e KARESHAFAA_DEMO_PASSWORD=... [-e DATABASE_URL=postgresql://...] kareshafaa
# Free-host friendly: listens on $PORT (Render/Cloud Run/Koyeb set it; default 8000). With SEED_AT_BUILD=1 (default)
# the initial records are prepared at BUILD time, so a cold start on a small free instance (0.1 vCPU / 512 MB) does
# not have to generate data or train models. Data persists only in PostgreSQL (DATABASE_URL) or on a persistent disk.
FROM python:3.12-slim
LABEL org.opencontainers.image.title="KareShafaa" \
      org.opencontainers.image.description="KareShafaa - closing the screening-to-care loop (prototype software, not a certified medical device)"
ARG SEED_AT_BUILD=1
ARG SEED_PATIENTS=2000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KARESHAFAA_DATA_DIR=/app/data KARESHAFAA_MODEL_DIR=/app/data/models
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 kareshafaa && mkdir -p /app/data/models && chown -R kareshafaa /app
COPY --chown=kareshafaa kareshafaa ./kareshafaa
# the signed-off rule tables: the case screen and the rule cards quote them to explain each decision
COPY --chown=kareshafaa docs/clinical_rules.md ./docs/clinical_rules.md
USER kareshafaa
# build-time secret only signs nothing that persists; the runtime secret comes from KARESHAFAA_SECRET_KEY
# The image carries the initial records (reference data, staff directory, sample patient records). On first start the
# bootstrap copies them into an EMPTY database (e.g. a new PostgreSQL database from DATABASE_URL); an existing
# database is never reset or overwritten.
RUN if [ "$SEED_AT_BUILD" = "1" ]; then \
      KARESHAFAA_SECRET_KEY=build-time-only KARESHAFAA_SWEEP_INTERVAL_SECONDS=0 KARESHAFAA_DATABASE_URL=sqlite:////app/data/kareshafaa.db \
        python -m kareshafaa.seed --patients "$SEED_PATIENTS" && \
      KARESHAFAA_SECRET_KEY=build-time-only KARESHAFAA_DATABASE_URL=sqlite:////app/data/kareshafaa.db python -m kareshafaa.bootstrap; \
    fi
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/readyz', timeout=4)"
# Start: prepare the database (create tables; fill an EMPTY database once; never reset existing data), then serve.
# KARESHAFAA_MODE=public_demo (internal showcase mode only) restores the bundled copy instead.
CMD ["sh", "-c", "python -m kareshafaa.bootstrap && python -m kareshafaa.public_demo && exec uvicorn kareshafaa.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_WORKERS:-4} --proxy-headers --forwarded-allow-ips='*'"]
