"""The Celery worker: picks up jobs and runs the whole pipeline in the background.

Start it from backend/ with (prefork, one job at a time, so time limits are enforced):
    celery -A app.worker worker --pool=prefork --concurrency=1 --queues=default --loglevel=info

Optional GPU worker for AI upsampling (its own Python 3.10 env with AudioSR, see docker/):
    WORKER_ROLE=gpu celery -A app.worker worker --pool=prefork --concurrency=1 --queues=gpu
"""
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

from app.ai_pipeline import enhance, export_preview
from app.celery_app import GPU_QUEUE, TASK_NAME, celery_app, enqueue_job
from app.config import get_settings
from app.dsp_diagnostics import analyze, load_audio
from app.errors import UserInputError
from app.job_store import FINISHED, JobStore, get_job_store
from app.storage import (
    StorageError,
    check_size,
    get_storage,
    is_valid_upload_key,
    output_key,
    preview_key,
)
from app.waveform import waveform_peaks

log = logging.getLogger(__name__)

__all__ = ["celery_app", "process_job"]

GENERIC_ERROR = "Something went wrong on our side while processing this file. Please try again later."
STORAGE_ERROR = "The file storage couldn't be reached. Please try again in a few minutes."

FINISHED_MESSAGES = {
    "bypassed": "Done: no repair was needed, only loudness and peaks were adjusted",
    "repaired": "Done: repaired and mastered",
    "partial": "Done, but not every repair was possible (see the notes)",
    "no_signal": "Done: the track is silent or too short, so nothing was changed",
}


# --------------------------------------------------------------------------- #
# Files come from and go to S3
# --------------------------------------------------------------------------- #
def _fetch_input(job: dict, workdir: Path) -> Path:
    """Download the user's upload into this job's temp folder, after checking it is the right one."""
    input_ref = job["input_ref"]
    if not is_valid_upload_key(input_ref):
        raise UserInputError("The upload reference is invalid.", "invalid_reference")

    storage = get_storage()
    info = storage.stat(input_ref)
    if info is None:
        raise UserInputError("The uploaded file was not found. Please upload it again.", "upload_missing")
    try:
        check_size(info.size)
    except ValueError as exc:
        raise UserInputError(str(exc), "too_large") from exc
    if job.get("input_etag") and info.etag != job["input_etag"]:
        raise UserInputError("The uploaded file changed after it was submitted. Please upload it again.",
                             "upload_changed")

    return storage.download(input_ref, workdir / f"input{Path(input_ref).suffix}")


def _safe(action, *args, **kwargs) -> None:
    """Job-store writes inside error handlers must never raise (e.g. Redis down)."""
    try:
        action(*args, **kwargs)
    except Exception:
        log.exception("Could not record job state (%s); the API will report it as stalled later",
                      getattr(action, "__name__", action))


# --------------------------------------------------------------------------- #
# The pipeline for one job
# --------------------------------------------------------------------------- #
def run_job(store: JobStore, job: dict, workdir: Path) -> str:
    settings = get_settings()
    job_id = job["job_id"]
    profile = settings.profile(job.get("loudness_profile"))
    stage_messages = {
        "diagnosing": "Checking the audio",
        "enhancing": "Repairing the audio",
        "mastering": f"Mastering for {profile.label}",
    }

    store.set_status(job_id, "downloading", "Fetching your upload")
    source = _fetch_input(job, workdir)

    store.set_status(job_id, "diagnosing", stage_messages["diagnosing"])
    audio = load_audio(source)                                  # decoded once, reused below
    report = analyze(audio=audio, profile_name=profile.name)
    store.update(job_id, diagnostics=report.to_dict(),
                 message=f"Health score {report.health_score} ({report.grade})")

    # AI upsampling needs a GPU worker: hand the job over instead of crawling on a CPU
    if settings.gpu_queue_enabled and settings.worker_role == "cpu" and report.flags.upsample:
        # A hand-over is not a failed attempt: give it back, so the GPU worker gets the full
        # JOB_MAX_ATTEMPTS and "attempt N of M" in the dashboard counts GPU tries only.
        attempts = (store.get(job_id) or {}).get("attempts", 1)
        store.update(job_id, status="queued", queue=GPU_QUEUE, attempts=max(0, attempts - 1),
                     message="Waiting for a GPU worker to restore the high frequencies")
        enqueue_job(job_id, queue=GPU_QUEUE)
        return "routed_to_gpu"

    result = enhance(
        source, workdir / "master.wav", report=report, audio=audio, profile_name=profile.name,
        on_stage=lambda stage: store.set_status(job_id, stage, stage_messages.get(stage)),
    )

    store.set_status(job_id, "uploading", "Saving the results")
    storage = get_storage()
    output_ref = storage.upload(result.output_path, output_key(job_id), content_type="audio/wav")

    previews = None
    waveforms = None
    try:   # browser-playable copies and drawings: nice to have, never worth failing the job for
        original = export_preview(audio.samples, audio.sample_rate, workdir / "original_preview.flac")
        enhanced = export_preview(result.mastered, result.output_sample_rate, workdir / "enhanced_preview.flac")
        previews = {
            "original": storage.upload(original, preview_key(job_id, "original"), "audio/flac"),
            "enhanced": storage.upload(enhanced, preview_key(job_id, "enhanced"), "audio/flac"),
        }
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        log.warning("Could not create playback previews for job %s", job_id, exc_info=True)
    try:
        waveforms = {"original": waveform_peaks(audio), "enhanced": waveform_peaks(result.mastered)}
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        log.warning("Could not compute waveforms for job %s", job_id, exc_info=True)

    store.update(job_id, status="completed", output_ref=output_ref, preview_refs=previews,
                 processing=result.to_public_dict(), waveforms=waveforms,
                 message=FINISHED_MESSAGES[result.outcome])
    return "completed"


@celery_app.task(name=TASK_NAME, bind=True, max_retries=2)
def process_job(self, job_id: str) -> dict:
    settings = get_settings()
    store = get_job_store()
    job = store.get(job_id)
    if job is None:
        log.warning("Job %s no longer exists; skipping", job_id)
        return {"job_id": job_id, "status": "missing"}
    if job["status"] in FINISHED:
        return {"job_id": job_id, "status": job["status"]}      # already done: do nothing twice

    # A file that crashes the worker (e.g. out of memory) would otherwise be redelivered forever
    attempts = store.begin_attempt(job_id)
    if attempts > settings.job_max_attempts:
        log.error("Job %s reached %d attempts; giving up", job_id, attempts)
        _safe(store.fail, job_id, "This file couldn't be processed after several attempts.", "too_many_attempts")
        return {"job_id": job_id, "status": "failed"}

    workdir = Path(tempfile.mkdtemp(prefix=f"agk-{job_id[:8]}-"))
    try:
        return {"job_id": job_id, "status": run_job(store, job, workdir)}

    except SoftTimeLimitExceeded:
        minutes = settings.job_soft_time_limit_seconds // 60
        log.error("Job %s hit the %d-minute limit", job_id, minutes)
        _safe(store.fail, job_id, f"Processing took longer than {minutes} minutes and was stopped.", "timeout")
        return {"job_id": job_id, "status": "failed"}

    except UserInputError as exc:                               # the file itself: a retry won't help
        log.warning("Job %s rejected: %s (%s)", job_id, exc.public_message, exc.code)
        _safe(store.fail, job_id, exc.public_message, exc.code)
        return {"job_id": job_id, "status": "failed"}

    except Exception as exc:
        ref = uuid.uuid4().hex[:8]                              # quote this to find the log entry
        transient = not (isinstance(exc, StorageError) and not exc.transient)
        if transient and self.request.retries < self.max_retries:
            log.warning("Job %s: temporary error (ref %s), retrying", job_id, ref, exc_info=True)
            try:   # queue the retry FIRST; only then tell the user we're retrying
                retry = self.retry(exc=exc, countdown=10 * (self.request.retries + 1), throw=False)
            except Exception:
                log.exception("Job %s: could not queue the retry (ref %s)", job_id, ref)
            else:
                _safe(store.update, job_id, status="queued", message="Retrying after a temporary problem")
                raise retry
        log.exception("Job %s failed (ref %s)", job_id, ref)
        public = STORAGE_ERROR if isinstance(exc, StorageError) else GENERIC_ERROR
        code = "storage_error" if isinstance(exc, StorageError) else "internal_error"
        _safe(store.fail, job_id, public, code, ref)
        return {"job_id": job_id, "status": "failed"}

    finally:
        shutil.rmtree(workdir, ignore_errors=True)               # always clean up temp files
