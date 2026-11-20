from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, found the same way no matter which folder uvicorn/celery is started from
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

ComputeDevice = Literal["auto", "cuda", "mps", "cpu"]
LoudnessProfileName = Literal["spotify", "youtube", "preserve", "custom"]


@dataclass(frozen=True)
class LoudnessProfile:
    """Where mastering aims. See docs/ARCHITECTURE.md for the sources behind each profile."""
    name: str
    label: str                       # shown to users
    target_lufs: float | None        # None = keep the track's own loudness ("preserve")
    ceiling_dbtp: float              # true-peak ceiling for normal masters
    loud_ceiling_dbtp: float | None  # stricter ceiling when the finished master is louder than -14 LUFS
    source: str                      # where the numbers come from (kept honest in the UI/docs)


LOUD_MASTER_LUFS = -14.0  # Spotify: masters louder than this should peak below -2 dBTP

PROFILES: dict[str, LoudnessProfile] = {
    # Spotify for Artists: -14 LUFS (ITU-R BS.1770), keep true peak below -1 dBTP,
    # or below -2 dBTP when the master is louder than -14 LUFS.
    "spotify": LoudnessProfile("spotify", "Spotify (-14 LUFS)", -14.0, -1.0, -2.0,
                               "Spotify for Artists: Loudness normalization"),
    # YouTube publishes no official figure; -14 LUFS is widely measured by third parties.
    "youtube": LoudnessProfile("youtube", "YouTube (-14 LUFS, unofficial)", -14.0, -1.0, None,
                               "Third-party measurements; YouTube publishes no official target"),
    # Keep the artist's loudness, only make the peaks safe for lossy encoding
    # (applying Spotify's -2 dBTP advice when the track is louder than -14 LUFS).
    "preserve": LoudnessProfile("preserve", "Keep loudness, fix peaks only", None, -1.0, -2.0,
                                "Peak safety only, per Spotify's true-peak advice"),
}


class Settings(BaseSettings):
    # Read values from backend/.env (if present) or from environment variables
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # ---------- Redis (job store + queue) ----------
    redis_url: str = "redis://localhost:6379/0"

    # ---------- Cloud storage (S3 or R2) ----------
    s3_bucket: str = "audio-gatekeeper-dev"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None      # set this only for Cloudflare R2 / other S3-compatible stores
    s3_access_key_id: str = ""              # leave empty to use an IAM role / default AWS credentials
    s3_secret_access_key: str = ""
    max_upload_bytes: int = Field(200 * 1024 * 1024, gt=0)   # 200 MB
    upload_link_seconds: int = Field(15 * 60, gt=0, le=7 * 24 * 3600)
    download_link_seconds: int = Field(30 * 60, gt=0, le=7 * 24 * 3600)

    # ---------- Limits on what the worker will decode ----------
    max_duration_seconds: float = Field(20 * 60, gt=0)       # a 200 MB MP3 could otherwise be hours long
    max_channels: int = Field(2, ge=1, le=5)                  # the BS.1770 meter here supports up to 5
    max_sample_rate: int = Field(192_000, ge=8_000)

    # ---------- DSP gatekeeper thresholds ----------
    rolloff_percent: float = Field(0.85, gt=0, lt=1)          # "85% of energy" point (reported only)
    rolloff_cutoff_hz: float = Field(15_000, gt=0)            # spectral cliff below this -> likely low-bitrate MP3
    clip_threshold: float = Field(0.99, gt=0, le=1)           # flat tops at >= this fraction of the file's peak
    crest_factor_min_db: float = Field(6.0, ge=0)             # below this -> squashed dynamics

    # ---------- Mastering ----------
    loudness_profile: LoudnessProfileName = "spotify"         # default profile when a job doesn't choose
    target_lufs: float = Field(-14.0, ge=-40, le=-5)          # used by the "custom" profile
    true_peak_ceiling_dbtp: float = Field(-1.0, ge=-12, le=0) # used by the "custom" profile
    target_sample_rate: Literal[44_100, 48_000, 96_000] = 48_000

    # ---------- Compute ----------
    compute_device: ComputeDevice = "auto"
    enable_audiosr: bool = True               # use AudioSR when it is installed AND a CUDA GPU is present
    allow_audiosr_without_cuda: bool = False  # AudioSR on CPU/MPS can take hours per song
    worker_role: Literal["cpu", "gpu"] = "cpu"
    gpu_queue_enabled: bool = False           # send AI-upsampling jobs to the "gpu" Celery queue

    # ---------- Jobs ----------
    job_soft_time_limit_seconds: int = Field(28 * 60, gt=0)
    job_time_limit_seconds: int = Field(30 * 60, gt=0)
    job_max_attempts: int = Field(3, ge=1, le=10)  # deliveries, incl. redeliveries after a worker crash
    job_ttl_seconds: int = Field(7 * 24 * 3600, gt=0)

    # ---------- Web API ----------
    cors_origins: list[str] = ["http://localhost:3000"]   # websites allowed to call the API
    api_key: str | None = None             # if set, every write request needs header X-API-Key
    rate_limit_per_minute: int = Field(30, ge=0)  # per client IP for POST endpoints; 0 = off
    trust_proxy_headers: bool = False      # use X-Forwarded-For for the client IP (only behind your own proxy)
    enable_docs: bool = True               # /docs and /openapi.json; switch off in production

    @field_validator("compute_device", mode="before")
    @classmethod
    def _lowercase_device(cls, value):
        return value.lower() if isinstance(value, str) else value

    def profile(self, name: str | None = None) -> LoudnessProfile:
        """The loudness profile for a job (or the default one)."""
        chosen = name or self.loudness_profile
        if chosen == "custom":
            return LoudnessProfile("custom", f"Custom ({self.target_lufs:g} LUFS)", self.target_lufs,
                                   self.true_peak_ceiling_dbtp, None, "Operator configuration")
        if chosen not in PROFILES:
            raise ValueError(f"Unknown loudness profile '{chosen}'")
        return PROFILES[chosen]


@lru_cache
def get_settings() -> Settings:
    return Settings()
