"""Step 17: job store. Where the worker writes progress and the API reads it."""
import json
import uuid
from datetime import datetime, timezone

import redis
from redis.exceptions import WatchError

from app.config import get_settings

KEY_PREFIX = "agk:job:"
TTL_SECONDS = 7 * 24 * 3600          # jobs are forgotten after a week

PROGRESS = {                          # what the dashboard's progress bar shows
    "queued": 0,
    "downloading": 10,
    "diagnosing": 25,
    "enhancing": 50,
    "mastering": 80,
    "uploading": 92,
    "completed": 100,
    "failed": 100,
}
FINISHED = ("completed", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, client: redis.Redis | None = None):
        self.r = client or redis.Redis.from_url(get_settings().redis_url, decode_responses=True)

    @staticmethod
    def _key(job_id: str) -> str:
        return f"{KEY_PREFIX}{job_id}"

    def create(self, original_filename: str, input_ref: str) -> dict:
        job = {
            "job_id": uuid.uuid4().hex,
            "status": "queued",
            "progress": 0,
            "message": "Waiting for a worker",
            "error": None,
            "original_filename": original_filename,
            "input_ref": input_ref,          # where the uploaded file lives
            "output_ref": None,              # where the mastered file will live
            "diagnostics": None,             # filled in by the worker
            "processing": None,              # filled in by the worker
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.r.set(self._key(job["job_id"]), json.dumps(job), ex=TTL_SECONDS)
        return job

    def get(self, job_id: str) -> dict | None:
        raw = self.r.get(self._key(job_id))
        return json.loads(raw) if raw else None

    def update(self, job_id: str, **changes) -> dict:
        """Change some fields safely, even if several processes write at once."""
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

    def fail(self, job_id: str, error: str) -> dict:
        return self.update(job_id, status="failed", message="Processing failed", error=error[:2000])
