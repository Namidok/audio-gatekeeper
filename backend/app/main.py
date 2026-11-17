"""Step 21: the web API. The browser talks only to this (and directly to S3 for the audio).

Start it from backend/ with:
    uvicorn app.main:app --reload --port 8000
"""
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from app.job_store import JobStore
from app.storage import (
    UPLOAD_LINK_SECONDS,
    Storage,
    StorageError,
    content_type_for,
    is_valid_upload_key,
    new_upload_key,
)
from app.worker import process_job

settings = get_settings()
app = FastAPI(title="AI Audio Quality Gatekeeper", version="1.0")

# Let the Next.js dashboard (a different website/port) call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


# --------------------------------------------------------------------------- #
# What the browser sends and gets back
# --------------------------------------------------------------------------- #
class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255, examples=["my_song.mp3"])
    size_bytes: int = Field(gt=0, examples=[8_400_000])


class UploadResponse(BaseModel):
    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str]          # the browser must send exactly these headers
    expires_in: int


class CreateJobRequest(BaseModel):
    object_key: str
    filename: str = Field(min_length=1, max_length=255)


class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str | None = None
    error: str | None = None
    original_filename: str
    diagnostics: dict | None = None
    processing: dict | None = None
    original_url: str | None = None  # signed playback link for the A/B player
    enhanced_url: str | None = None  # only once the job is completed
    created_at: str
    updated_at: str


def _job_response(job: dict) -> JobResponse:
    storage = Storage()
    stem = Path(job["original_filename"]).stem
    return JobResponse(
        job_id=job["job_id"],
        status=job["status"],
        progress=job["progress"],
        message=job["message"],
        error=job["error"],
        original_filename=job["original_filename"],
        diagnostics=job["diagnostics"],
        processing=job["processing"],
        original_url=storage.presign_download(job["input_ref"], job["original_filename"]),
        enhanced_url=(storage.presign_download(job["output_ref"], f"{stem}_mastered.wav")
                      if job["status"] == "completed" and job["output_ref"] else None),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict:
    """Quick check that the API is up and can reach Redis."""
    try:
        redis_ok = bool(JobStore().r.ping())
    except Exception:
        redis_ok = False
    return {"status": "ok" if redis_ok else "degraded", "redis": redis_ok}


@app.post("/uploads", response_model=UploadResponse)
def create_upload(body: UploadRequest) -> UploadResponse:
    """Step 1 of the flow: give the browser a signed link to upload one file straight to S3."""
    try:
        _, content_type = content_type_for(body.filename)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc))

    key = new_upload_key(body.filename)
    try:
        url = Storage().presign_upload(key, content_type, body.size_bytes)
    except ValueError as exc:                                  # file too big
        raise HTTPException(status_code=413, detail=str(exc))

    return UploadResponse(object_key=key, upload_url=url,
                          headers={"Content-Type": content_type},
                          expires_in=UPLOAD_LINK_SECONDS)


@app.post("/jobs", response_model=JobResponse, status_code=202)
def create_job(body: CreateJobRequest) -> JobResponse:
    """Step 2: the upload finished, so queue the file for processing."""
    if not is_valid_upload_key(body.object_key):
        raise HTTPException(status_code=400, detail="Invalid object key")
    try:
        uploaded = Storage().size_of(body.object_key)
    except StorageError:
        raise HTTPException(status_code=502, detail="Storage is not reachable right now")
    if uploaded is None:
        raise HTTPException(status_code=400, detail="File not found in storage: upload it first")

    store = JobStore()
    job = store.create(Path(body.filename).name, body.object_key)
    task = process_job.delay(job["job_id"])                    # returns immediately
    job = store.update(job["job_id"], celery_task_id=task.id)
    return _job_response(job)


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str) -> JobResponse:
    """Step 3: the dashboard polls this every second or two for progress and results."""
    job = JobStore().get(job_id) if JOB_ID_PATTERN.match(job_id) else None
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)
