"""Job store: where the worker writes progress and the API reads it (Redis, JSON per job)."""
import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal, get_args

import redis
from redis.exceptions import WatchError

from app.config import get_settings

KEY_PREFIX = "agk:job:"
UPLOAD_CLAIM_PREFIX = "agk:upload:"   # object key -> job id, so one upload can't start many jobs

JobStatus = Literal["queued", "downloading", "diagnosing", "enhancing", "mastering",
                    "uploading", "completed", "failed"]
PROGRESS: dict[str, int] = {          # what the dashboard's progress bar shows
    "queued": 0,
    "downloading": 10,
    "diagnosing": 25,
    "enhancing": 50,
    "mastering": 80,
    "uploading": 92,
    "completed": 100,
    "failed": 100,
}
assert set(PROGRESS) == set(get_args(JobStatus))
FINISHED = ("completed", "failed")

# Fields update() may change. Anything else is a bug (e.g. a typo) and is refused.
UPDATABLE = {
    "status", "progress", "message", "error", "error_code", "error_ref", "output_ref",
    "preview_refs", "diagnostics", "processing", "waveforms", "attempts", "queue",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seconds_since(iso_time: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_time)).total_seconds()


class JobStore:
    def __init__(self, client: redis.Redis | None = None):
        self.r = client or redis.Redis.from_url(get_settings().redis_url, decode_responses=True,
                                                socket_timeout=5, socket_connect_timeout=5)
        self.ttl = get_settings().job_ttl_seconds

    @staticmethod
    def _key(job_id: str) -> str:
        return f"{KEY_PREFIX}{job_id}"

    def create(self, original_filename: str, input_ref: str, *, input_etag: str = "",
               loudness_profile: str = "spotify") -> dict:
        job = {
            "job_id": uuid.uuid4().hex,
            "status": "queued",
            "progress": 0,
            "message": "Waiting for a worker",
            "error": None,                   # public, safe-to-show message
            "error_code": None,              # machine-readable reason
            "error_ref": None,               # reference to find the full error in the server logs
            "original_filename": original_filename,
            "input_ref": input_ref,          # where the uploaded file lives (never sent to the browser)
            "input_etag": input_etag,        # the exact upload that was submitted
            "loudness_profile": loudness_profile,
            "output_ref": None,              # where the mastered file will live
            "preview_refs": None,            # browser-playable copies
            "diagnostics": None,             # filled in by the worker
            "processing": None,              # filled in by the worker
            "waveforms": None,
            "attempts": 0,                   # how many times a worker has started this job
            "queue": "default",
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.r.set(self._key(job["job_id"]), json.dumps(job), ex=self.ttl)
        return job

    def claim_upload(self, object_key: str, job_id: str) -> str | None:
        """Atomically tie an upload to a job. Returns the existing job id if it was already claimed."""
        key = f"{UPLOAD_CLAIM_PREFIX}{object_key}"
        if self.r.set(key, job_id, nx=True, ex=self.ttl):
            return None
        return self.r.get(key)

    def release_upload(self, object_key: str) -> None:
        self.r.delete(f"{UPLOAD_CLAIM_PREFIX}{object_key}")

    def get(self, job_id: str) -> dict | None:
        raw = self.r.get(self._key(job_id))
        return json.loads(raw) if raw else None

    def update(self, job_id: str, **changes) -> dict:
        """Change some fields safely, even if several processes write at once."""
        unknown = set(changes) - UPDATABLE
        if unknown:
            raise KeyError(f"Not an updatable job field: {', '.join(sorted(unknown))}")
        key = self._key(job_id)
        while True:
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(key)                       # 1. tell Redis: warn me if this changes
                    raw = pipe.get(key)
                    if raw is None:
                        raise KeyError(f"Job {job_id} not found")
                    job = json.loads(raw)

                    new_status = changes.get("status")
                    if job["status"] in FINISHED and new_status and new_status != job["status"]:
                        return job                        # a finished job stays finished
                    if new_status:
                        if new_status not in PROGRESS:
                            raise ValueError(f"Unknown status '{new_status}'")
                        changes.setdefault("progress", PROGRESS[new_status])

                    job.update(changes)
                    job["updated_at"] = _now()

                    pipe.multi()                          # 2. save, but only if nobody
                    pipe.set(key, json.dumps(job), keepttl=True)   # changed it meanwhile
                    pipe.execute()
                    return job
                except WatchError:
                    continue                              # 3. someone did: read again, retry

    def set_status(self, job_id: str, status: str, message: str | None = None) -> dict:
        return self.update(job_id, status=status, message=message)

    def begin_attempt(self, job_id: str) -> int:
        """Count one more start of this job (incl. redeliveries after a worker crash)."""
        job = self.get(job_id)
        attempts = (job or {}).get("attempts", 0) + 1
        self.update(job_id, attempts=attempts)
        return attempts

    def fail(self, job_id: str, error: str, code: str = "internal_error", ref: str | None = None) -> dict:
        """Mark a job failed. `error` must be safe to show to the user."""
        return self.update(job_id, status="failed", message="Processing failed",
                           error=error[:2000], error_code=code, error_ref=ref)

    def delete(self, job_id: str) -> None:
        self.r.delete(self._key(job_id))

    def ping(self) -> bool:
        return bool(self.r.ping())


def is_stalled(job: dict) -> bool:
    """True if an unfinished job hasn't moved for longer than any job is allowed to run."""
    s = get_settings()
    if job["status"] in FINISHED:
        return False
    limit = s.job_time_limit_seconds + 300          # hard time limit + 5 minutes grace
    if job["status"] == "queued" and job.get("attempts", 0) == 0:
        limit = max(limit, 6 * 3600)                # waiting in a long queue is not a stall
    return seconds_since(job["updated_at"]) > limit


@lru_cache(maxsize=1)
def get_job_store() -> JobStore:
    """One Redis connection pool per process."""
    return JobStore()
