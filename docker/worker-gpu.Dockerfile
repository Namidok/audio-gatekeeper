# GPU worker with AudioSR (AI bandwidth extension). Consumes ONLY the "gpu" queue.
# Separate image because AudioSR pins numpy<=1.23.5, which the main stack can't use.
#
# STATUS: written but NOT built or tested in this repository (no CUDA host available).
# Pin the versions you verify on your GPU host in backend/requirements-gpu.txt.
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends python3.10 python3-pip libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /srv
COPY backend/requirements-gpu.txt .
RUN python3.10 -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121 \
    && python3.10 -m pip install -r requirements-gpu.txt
COPY backend/app ./app

USER app
ENV COMPUTE_DEVICE=cuda WORKER_ROLE=gpu ENABLE_AUDIOSR=true
CMD ["python3.10", "-m", "celery", "-A", "app.worker", "worker", "--pool=prefork", "--concurrency=1", "--queues=gpu", "--loglevel=info"]
