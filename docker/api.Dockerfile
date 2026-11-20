# API image: FastAPI + the Celery client (jobs are queued by name; torch is never imported here).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 app

WORKDIR /srv
COPY backend/requirements.txt .
# The API only needs the web/queue/storage packages, not the DSP stack
RUN grep -E '^(fastapi|uvicorn|pydantic|pydantic-settings|celery|redis|boto3)' requirements.txt > api-requirements.txt \
    && pip install -r api-requirements.txt
COPY backend/app ./app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
