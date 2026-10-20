# Wasl prototype image (synthetic data only). Build: docker build -t wasl . ; run: docker run -p 8000:8000 wasl
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WASL_DATA_DIR=/app/data WASL_MODEL_DIR=/app/data/models
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wasl ./wasl
RUN useradd --create-home --uid 10001 wasl && mkdir -p /app/data/models && chown -R wasl /app
USER wasl
EXPOSE 8000
# Seeds a fresh synthetic database on first start (if none exists), then serves.
CMD ["sh", "-c", "[ -f /app/data/wasl.db ] || python -m wasl.seed; exec uvicorn wasl.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_WORKERS:-4}"]
