"""Step 18 + 20: the Celery worker. Picks up jobs and runs the whole pipeline in the background.

Start it from backend/ with:
    celery -A app.worker worker --pool=solo --loglevel=info
"""
import logging
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from app.ai_pipeline import enhance
from app.config import get_settings
from app.dsp_diagnostics import analyze
from app.job_store import JobStore
from app.storage import Storage, is_valid_upload_key, output_key

log = logging.getLogger(__name__)
settings = get_settings()


def _redis_db(url: str, db: int) -> str:
    """Same Redis server, different numbered database (0 = jobs, 1 = queue, 2 = results)."""
    base = url.rsplit("/", 1)[0] if url.count("/") > 2 else url
    return f"{base}/{db}"


celery_app = Celery(
    "audio_gatekeeper",
    broker=_redis_db(settings.redis_url, 1),
    backend=_redis_db(settings.redis_url, 2),
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,              # only remove a job from the queue once it is finished
    task_reject_on_worker_lost=True,  # if the worker crashes mid-job, put the job back
    worker_prefetch_multiplier=1,     # take one long job at a time, not a handful
    task_soft_time_limit=28 * 60,     # politely stop a job after 28 minutes...
    task_time_limit=30 * 60,          # ...and force-stop it after 30
    result_expires=24 * 3600,
    broker_connection_retry_on_startup=True,
)

STAGE_MESSAGES = {
    "diagnosing": "Running DSP diagnostics",
    "enhancing": "Repairing audio",
    "mastering": "Mastering to -14 LUFS / -1 dBTP",
}


# --------------------------------------------------------------------------- #
# Step 20: files now come from and go to S3
# --------------------------------------------------------------------------- #
def _fetch_input(input_ref: str, workdir: Path) -> Path:
    """Download the user's upload from the bucket into this job's temp folder."""
    if not is_valid_upload_key(input_ref):
        raise ValueError("Invalid upload reference")          # not a key we created: refuse

    storage = Storage()
    size = storage.size_of(input_ref)
    if size is None:
        raise FileNotFoundError("The uploaded file was not found in storage (upload may have failed)")
    if size > settings.max_upload_bytes:
        raise ValueError(f"File is {size / 1_048_576:.0f} MB; the limit is {settings.max_upload_bytes / 1_048_576:.0f} MB")

    target = workdir / f"input{Path(input_ref).suffix}"
    return storage.download(input_ref, target)


def _save_output(output: Path, job_id: str) -> str:
    """Upload the mastered WAV to the bucket and return where it lives."""
    return Storage().upload(output, output_key(job_id), content_type="audio/wav")


# --------------------------------------------------------------------------- #
# The job itself (unchanged from Step 18)
# --------------------------------------------------------------------------- #
@celery_app.task(name="process_job", bind=True, max_retries=2)
def process_job(self, job_id: str) -> dict:
    store = JobStore()
    job = store.get(job_id)
    if job is None:
        log.warning("Job %s no longer exists; skipping", job_id)
        return {"job_id": job_id, "status": "missing"}
    if job["status"] in ("completed", "failed"):
        return {"job_id": job_id, "status": job["status"]}      # already done: do nothing twice

    workdir = Path(tempfile.mkdtemp(prefix=f"agk-{job_id[:8]}-"))
    try:
        store.set_status(job_id, "downloading", "Fetching the upload")
        source = _fetch_input(job["input_ref"], workdir)

        store.set_status(job_id, "diagnosing", STAGE_MESSAGES["diagnosing"])
        report = analyze(source)
        store.update(job_id, diagnostics=report.to_dict(),
                     message=f"Health score {report.health_score} ({report.grade})")

        result = enhance(
            source, workdir / "master.wav", report=report,
            on_stage=lambda stage: store.set_status(job_id, stage, STAGE_MESSAGES.get(stage)),
        )

        store.set_status(job_id, "uploading", "Saving the mastered file")
        output_ref = _save_output(result.output_path, job_id)

        processing = asdict(result)
        processing["output_path"] = str(processing["output_path"])
        store.update(job_id, status="completed", output_ref=output_ref, processing=processing,
                     message="AI skipped: track was already healthy" if result.ai_bypassed else "Done")
        return {"job_id": job_id, "status": "completed"}

    except SoftTimeLimitExceeded:
        store.fail(job_id, "Processing took longer than 28 minutes and was stopped")
        raise
    except (FileNotFoundError, ValueError) as exc:
        message = str(exc).replace(f"{workdir}/", "")           # hide our temp folder path
        store.fail(job_id, message)                              # bad input: a retry won't help
        return {"job_id": job_id, "status": "failed"}
    except Exception as exc:
        if self.request.retries < self.max_retries:              # maybe temporary: try again
            store.set_status(job_id, "queued", f"Retrying after an error ({exc.__class__.__name__})")
            raise self.retry(exc=exc, countdown=10)
        log.exception("Job %s failed after %d retries", job_id, self.max_retries)
        store.fail(job_id, f"{exc.__class__.__name__}: {exc}")
        return {"job_id": job_id, "status": "failed"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)               # always clean up temp files
