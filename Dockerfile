# KareShafaa prototype image (synthetic data only). Build: docker build -t kareshafaa . ; run: docker run -p 8000:8000 kareshafaa
FROM python:3.12-slim
LABEL org.opencontainers.image.title="KareShafaa" \
      org.opencontainers.image.description="KareShafaa - closing the screening-to-care loop (prototype, synthetic data only)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KARESHAFAA_DATA_DIR=/app/data KARESHAFAA_MODEL_DIR=/app/data/models
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kareshafaa ./kareshafaa
RUN useradd --create-home --uid 10001 kareshafaa && mkdir -p /app/data/models && chown -R kareshafaa /app
USER kareshafaa
EXPOSE 8000
# Seeds a fresh synthetic database on first start (if none exists), then serves.
CMD ["sh", "-c", "[ -f /app/data/kareshafaa.db ] || python -m kareshafaa.seed; exec uvicorn kareshafaa.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_WORKERS:-4}"]
