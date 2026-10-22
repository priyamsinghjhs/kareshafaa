# KareShafaa prototype image (synthetic data only). Build: docker build -t kareshafaa . ; run: docker run -p 8000:8000 kareshafaa
# Free-host friendly: listens on $PORT (Render/Cloud Run/Koyeb set it; default 8000) and, with SEED_AT_BUILD=1
# (default), seeds the synthetic database and its public-demo seed copy at BUILD time, so a cold start on a small
# free instance (0.1 vCPU / 512 MB) does not have to generate data or train models.
FROM python:3.12-slim
LABEL org.opencontainers.image.title="KareShafaa" \
      org.opencontainers.image.description="KareShafaa - closing the screening-to-care loop (prototype, synthetic data only)"
ARG SEED_AT_BUILD=1
ARG SEED_PATIENTS=2000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KARESHAFAA_DATA_DIR=/app/data KARESHAFAA_MODEL_DIR=/app/data/models
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 kareshafaa && mkdir -p /app/data/models && chown -R kareshafaa /app
COPY --chown=kareshafaa kareshafaa ./kareshafaa
USER kareshafaa
# build-time secret only signs nothing that persists; the runtime secret comes from KARESHAFAA_SECRET_KEY
RUN if [ "$SEED_AT_BUILD" = "1" ]; then \
      KARESHAFAA_SECRET_KEY=build-time-only KARESHAFAA_SWEEP_INTERVAL_SECONDS=0 \
        python -m kareshafaa.seed --patients "$SEED_PATIENTS" && \
      KARESHAFAA_SECRET_KEY=build-time-only KARESHAFAA_PUBLIC_DEMO=1 python -m kareshafaa.public_demo; \
    fi
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz', timeout=4)"
# Seeds a fresh synthetic database on first start (if none exists); in public demo mode (KARESHAFAA_PUBLIC_DEMO=1)
# restores it from the seed copy; then serves.
CMD ["sh", "-c", "[ -f /app/data/kareshafaa.db ] || python -m kareshafaa.seed; python -m kareshafaa.public_demo; exec uvicorn kareshafaa.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_WORKERS:-4} --proxy-headers --forwarded-allow-ips='*'"]
