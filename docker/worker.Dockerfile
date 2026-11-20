# CPU worker: DSP gatekeeper, de-clip, resample, mastering. Consumes the "default" queue.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /srv
COPY backend/requirements.txt .
# CPU-only torch wheels: a much smaller image than the default CUDA build
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY backend/app ./app

USER app
ENV COMPUTE_DEVICE=cpu WORKER_ROLE=cpu
# prefork + concurrency 1: one job at a time per container, and Celery's time limits are enforced
CMD ["celery", "-A", "app.worker", "worker", "--pool=prefork", "--concurrency=1", "--queues=default", "--loglevel=info"]
