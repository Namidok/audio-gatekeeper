"""The web API. The browser talks only to this (and directly to S3 for the audio).

Start it from backend/ with:
    uvicorn app.main:app --reload --port 8000

This module never imports torch or the audio pipeline: jobs are queued by task name.
"""
import logging
import re
from pathlib import Path

import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.celery_app import enqueue_job, redis_db_url
from app.config import PROFILES, get_settings
from app.job_store import FINISHED, JobStore, get_job_store, is_stalled
from app.schemas import (
    CreateJobRequest,
    ErrorResponse,
    HealthResponse,
    JobResponse,
    ProfileInfo,
    UploadRequest,
    UploadResponse,
)
from app.security import rate_limit, require_api_key
from app.storage import (
    Storage,
    StorageError,
    check_size,
    content_type_for,
    get_storage,
    is_valid_upload_key,
    new_upload_key,
    safe_display_name,
)

log = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="AI Audio Quality Gatekeeper",
    version="1.1",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)

# Let the Next.js dashboard (a different website/port) call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
    expose_headers=["Retry-After"],
)

JOB_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
WRITE_GUARDS = [Depends(require_api_key), Depends(rate_limit)]
ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
          413: {"model": ErrorResponse},
          415: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 502: {"model": ErrorResponse},
          503: {"model": ErrorResponse}}


# --------------------------------------------------------------------------- #
# Infrastructure failures become clear 5xx answers (with CORS headers) instead of bare 500s
# --------------------------------------------------------------------------- #
@app.exception_handler(redis.exceptions.RedisError)
def _redis_down(request: Request, exc: redis.exceptions.RedisError) -> JSONResponse:
    log.error("Redis error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "The job database is unavailable. Please try again shortly."})


@app.exception_handler(StorageError)
def _storage_down(request: Request, exc: StorageError) -> JSONResponse:
    log.error("Storage error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": "File storage is not reachable right now. Please try again shortly."})


# --------------------------------------------------------------------------- #
# Building the job answer (signed links are created fresh on every read)
# --------------------------------------------------------------------------- #
def _job_response(job: dict, storage: Storage) -> JobResponse:
    s = get_settings()
    name = job["original_filename"]
    stem = Path(name).stem
    previews = job.get("preview_refs") or {}
    completed = job["status"] == "completed" and bool(job.get("output_ref"))
    return JobResponse(
        job_id=job["job_id"],
        status=job["status"],
        progress=job["progress"],
        message=job.get("message"),
        error=job.get("error"),
        error_code=job.get("error_code"),
        error_ref=job.get("error_ref"),
        original_filename=name,
        loudness_profile=job.get("loudness_profile") or s.loudness_profile,
        diagnostics=job.get("diagnostics"),
        processing=job.get("processing"),
        original_url=storage.presign_download(previews.get("original") or job["input_ref"], name),
        enhanced_url=(storage.presign_download(previews.get("enhanced") or job["output_ref"],
                                               f"{stem}_mastered") if completed else None),
        download_url=(storage.presign_download(job["output_ref"], f"{stem}_mastered.wav", attachment=True)
                      if completed else None),
        links_expire_in=s.download_link_seconds,
        queue=job.get("queue") or "default",
        attempts=job.get("attempts", 0),
        max_attempts=s.job_max_attempts,
        waveforms=job.get("waveforms"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
    )


def _load_job(job_id: str, store: JobStore) -> dict:
    job = store.get(job_id) if JOB_ID_PATTERN.fullmatch(job_id) else None
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found. It may have expired (jobs are kept for 7 days).")
    return job


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse)
def health(full: bool = Query(False, description="Also check S3 access (one HEAD request)"),
           store: JobStore = Depends(get_job_store)) -> HealthResponse:
    """Is the API up, and can it reach Redis, the job queue and (optionally) storage?"""
    def check(fn) -> bool:
        try:
            fn()
            return True
        except Exception as exc:
            log.warning("Health check failed: %s", exc.__class__.__name__)
            return False

    redis_ok = check(store.ping)
    queue_ok = check(lambda: redis.Redis.from_url(redis_db_url(get_settings().redis_url, 1),
                                                  socket_timeout=3, socket_connect_timeout=3).ping())
    storage_ok = check(lambda: get_storage().check_access()) if full else None
    healthy = redis_ok and queue_ok and storage_ok is not False
    return HealthResponse(status="ok" if healthy else "degraded", redis=redis_ok, queue=queue_ok,
                          storage=storage_ok)


@app.get("/profiles", response_model=list[ProfileInfo])
def profiles() -> list[ProfileInfo]:
    """Loudness profiles a job can be mastered for, and where their numbers come from."""
    s = get_settings()
    return [ProfileInfo(name=p.name, label=p.label, target_lufs=p.target_lufs, ceiling_dbtp=p.ceiling_dbtp,
                        source=p.source) for p in (*PROFILES.values(), s.profile("custom"))]


@app.post("/uploads", response_model=UploadResponse, dependencies=WRITE_GUARDS, responses=ERRORS)
def create_upload(body: UploadRequest, storage: Storage = Depends(get_storage)) -> UploadResponse:
    """Step 1: give the browser a signed link to upload one file straight to S3."""
    try:
        _, content_type = content_type_for(body.filename)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc))

    key = new_upload_key(body.filename)
    try:
        url = storage.presign_upload(key, content_type, body.size_bytes)
    except ValueError as exc:                                  # file too big
        raise HTTPException(status_code=413, detail=str(exc))

    return UploadResponse(object_key=key, upload_url=url, headers={"Content-Type": content_type},
                          expires_in=get_settings().upload_link_seconds)


@app.post("/jobs", response_model=JobResponse, status_code=202, dependencies=WRITE_GUARDS, responses=ERRORS)
def create_job(body: CreateJobRequest, store: JobStore = Depends(get_job_store),
               storage: Storage = Depends(get_storage)) -> JobResponse:
    """Step 2: the upload finished, so queue the file for processing.

    Idempotent per upload: sending the same object_key again returns the existing job.
    """
    if not is_valid_upload_key(body.object_key):
        raise HTTPException(status_code=400, detail="Invalid object key")
    info = storage.stat(body.object_key)
    if info is None:
        raise HTTPException(status_code=400, detail="File not found in storage: upload it first")
    try:
        check_size(info.size)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))

    profile = body.loudness_profile or get_settings().loudness_profile
    job = store.create(safe_display_name(body.filename), body.object_key, input_etag=info.etag,
                       loudness_profile=profile)
    existing_id = store.claim_upload(body.object_key, job["job_id"])
    if existing_id is not None:
        store.delete(job["job_id"])
        existing = store.get(existing_id)
        if existing is not None and existing["status"] != "failed":
            return _job_response(existing, storage)          # same upload sent twice: same job
        store.release_upload(body.object_key)                 # the old job expired or failed: start over
        job = store.create(safe_display_name(body.filename), body.object_key, input_etag=info.etag,
                           loudness_profile=profile)
        store.claim_upload(body.object_key, job["job_id"])

    try:
        enqueue_job(job["job_id"])                            # returns immediately
    except Exception:
        log.exception("Could not queue job %s", job["job_id"])
        store.fail(job["job_id"], "The processing queue is unavailable. Please try again in a minute.",
                   "queue_unavailable")
        store.release_upload(body.object_key)                 # let the user retry with the same upload
        raise HTTPException(status_code=503, detail="The processing queue is unavailable. Please try again in a minute.")
    return _job_response(job, storage)


@app.get("/jobs/{job_id}", response_model=JobResponse, responses=ERRORS)
def get_job(job_id: str, store: JobStore = Depends(get_job_store),
            storage: Storage = Depends(get_storage)) -> JobResponse:
    """Step 3: the dashboard polls this for progress and results (signed links are fresh every time)."""
    job = _load_job(job_id, store)
    if is_stalled(job):
        job = store.fail(job_id, "The worker stopped responding while processing this file. Please try again.",
                         "stalled")
    return _job_response(job, storage)


@app.delete("/jobs/{job_id}", status_code=204, dependencies=WRITE_GUARDS, responses=ERRORS)
def delete_job(job_id: str, store: JobStore = Depends(get_job_store),
               storage: Storage = Depends(get_storage)) -> Response:
    """Delete a job and every file it created (the upload, the master and the previews).

    Only finished jobs: a running worker would otherwise write its results back after the delete.
    """
    job = _load_job(job_id, store)
    if job["status"] not in FINISHED:
        raise HTTPException(status_code=409, detail="The job is still processing. Delete it once it has finished.")
    keys = [job["input_ref"], job.get("output_ref"), *(job.get("preview_refs") or {}).values()]
    for key in filter(None, keys):
        storage.delete(key)
    store.release_upload(job["input_ref"])
    store.delete(job_id)
    return Response(status_code=204)
