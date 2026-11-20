"""The Celery app, kept free of heavy imports (no torch, no librosa).

The API imports only this module to queue jobs *by name*, so the API process stays small and
starts fast. The task itself lives in app.worker, which only workers import.
"""
from urllib.parse import urlsplit, urlunsplit

from celery import Celery

from app.config import get_settings

TASK_NAME = "process_job"
DEFAULT_QUEUE = "default"
GPU_QUEUE = "gpu"


def redis_db_url(url: str, db: int) -> str:
    """Same Redis server, different numbered database. Keeps credentials and ?query options (TLS)."""
    parts = urlsplit(url)
    if parts.scheme == "unix":                      # unix sockets select the db with ?db=N
        query = "&".join(p for p in parts.query.split("&") if p and not p.startswith("db="))
        return f"unix://{parts.netloc}{parts.path}?{query + '&' if query else ''}db={db}"
    return urlunsplit(parts._replace(path=f"/{db}"))


def make_celery() -> Celery:
    s = get_settings()
    app = Celery("audio_gatekeeper", broker=redis_db_url(s.redis_url, 1))  # 0 = jobs, 1 = queue
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,          # results live in the job store, not in Celery
        task_acks_late=True,              # only remove a job from the queue once it is finished...
        task_reject_on_worker_lost=True,  # ...and put it back if the worker dies mid-job
        worker_prefetch_multiplier=1,     # take one long job at a time, not a handful
        task_soft_time_limit=s.job_soft_time_limit_seconds,  # politely stop a job...
        task_time_limit=s.job_time_limit_seconds,            # ...then force-stop it (needs prefork)
        # Must be longer than the longest job, or Redis hands a running job to a second worker
        broker_transport_options={"visibility_timeout": s.job_time_limit_seconds + 600},
        task_default_queue=DEFAULT_QUEUE,
        worker_max_tasks_per_child=50,    # recycle the process now and then (frees fragmented memory)
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = make_celery()


def enqueue_job(job_id: str, queue: str = DEFAULT_QUEUE) -> str:
    """Queue a job by task name. Raises kombu/redis errors if the broker is unreachable."""
    return celery_app.send_task(TASK_NAME, args=[job_id], queue=queue).id
