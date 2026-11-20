"""Typed API contract (mirrored by frontend/lib/types.ts).

New fields always have defaults, so jobs stored before a change (Redis keeps them for a week)
still serialise. Unknown stored fields are ignored, never sent to the browser.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.job_store import JobStatus

Severity = Literal["info", "warning", "critical"]
Grade = Literal["A", "B", "C", "D", "F"]
Outcome = Literal["bypassed", "repaired", "partial", "no_signal"]
UpsampleMethod = Literal["audiosr", "resample_only", "none"]
ProfileName = Literal["spotify", "youtube", "preserve", "custom"]


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------- request bodies ----------
class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255, examples=["my_song.mp3"])
    size_bytes: int = Field(gt=0, examples=[8_400_000])


class UploadResponse(BaseModel):
    object_key: str
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]          # the browser must send exactly these headers
    expires_in: int


class CreateJobRequest(BaseModel):
    object_key: str = Field(max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    loudness_profile: ProfileName | None = None   # None = the server's default profile


# ---------- diagnostics ----------
class Flags(_Lenient):
    upsample: bool
    declip: bool


class Clipping(_Lenient):
    clipped_samples: int
    clipped_ratio: float
    clip_events: int
    clip_level: float = 0.0
    is_clipped: bool


class Dynamics(_Lenient):
    peak_dbfs: float
    rms_dbfs: float
    crest_factor_db: float
    is_squashed: bool


class Bandwidth(_Lenient):
    rolloff_85_hz: float
    cutoff_hz: float
    nyquist_hz: float
    is_band_limited: bool
    limited_by_sample_rate: bool = False


class Loudness(_Lenient):
    integrated_lufs: float | None
    gain_to_target_db: float | None


class IssueModel(_Lenient):
    code: str
    severity: Severity
    message: str
    penalty: int


class Diagnostics(_Lenient):
    health_score: int = Field(ge=0, le=100)
    grade: Grade
    flags: Flags
    sample_rate: int
    channels: int
    duration_seconds: float
    clipping: Clipping
    dynamics: Dynamics
    bandwidth: Bandwidth
    loudness: Loudness
    has_signal: bool = True
    issues: list[IssueModel]
    analysis_seconds: float


# ---------- processing ----------
class StepModel(_Lenient):
    kind: Literal["declip", "bandwidth_extension", "resample", "master"]
    status: Literal["applied", "skipped", "fallback"]
    summary: str
    detail: str = ""


class OutputMetrics(_Lenient):
    clipped_ratio: float
    cutoff_hz: float
    nyquist_hz: float


class Processing(_Lenient):
    device: str
    ai_bypassed: bool
    outcome: Outcome = "bypassed"
    upsample_method: UpsampleMethod = "none"
    highs_restored: bool = False
    steps: list[StepModel] = []
    steps_applied: list[str] = []
    steps_skipped: list[str] = []
    notes: list[str] = []
    loudness_profile: str = "spotify"
    loudness_profile_label: str = ""
    target_lufs: float | None = -14.0
    ceiling_dbtp: float = -1.0
    output_sample_rate: int
    output_bit_depth: int
    input_lufs: float | None = None
    output_lufs: float | None = None
    input_true_peak_dbtp: float | None = None
    output_true_peak_dbtp: float | None = None
    limiter_reduction_db: float = 0.0
    output_metrics: OutputMetrics | None = None
    processing_seconds: float


class Waveforms(_Lenient):
    original: list[float]
    enhanced: list[float]


# ---------- the job ----------
class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str | None = None
    error: str | None = None          # safe to show; never contains server internals
    error_code: str | None = None
    error_ref: str | None = None      # quote this to support to find the server log entry
    original_filename: str
    loudness_profile: str = "spotify"
    diagnostics: Diagnostics | None = None
    processing: Processing | None = None
    original_url: str | None = None   # signed playback link (browser-friendly preview when available)
    enhanced_url: str | None = None   # signed playback link, only once the job is completed
    download_url: str | None = None   # signed "save as" link for the 24-bit WAV
    links_expire_in: int | None = None  # seconds until the signed links above stop working
    queue: Literal["default", "gpu"] = "default"  # "gpu": handed to the AI worker for high-frequency repair
    attempts: int = Field(0, ge=0)      # how many times a worker has started this job (on its current queue)
    max_attempts: int = Field(1, ge=1)  # after this many starts the job is failed
    waveforms: Waveforms | None = None
    created_at: str
    updated_at: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    redis: bool
    queue: bool
    storage: bool | None = None       # None = not checked


class ProfileInfo(BaseModel):
    name: str
    label: str
    target_lufs: float | None
    ceiling_dbtp: float
    source: str


class ErrorResponse(BaseModel):
    detail: str
