from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read values from a .env file (if present) or from environment variables
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---------- Redis (job queue) ----------
    redis_url: str = "redis://localhost:6379/0"

    # ---------- Cloud storage (S3 or R2) ----------
    s3_bucket: str = "audio-gatekeeper-dev"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None      # set this only for Cloudflare R2
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    max_upload_bytes: int = 200 * 1024 * 1024   # 200 MB

    # ---------- DSP gatekeeper thresholds ----------
    rolloff_percent: float = 0.85        # "85% of energy" point
    rolloff_cutoff_hz: float = 15_000    # below this → likely a compressed MP3
    clip_threshold: float = 0.99         # samples at or above this count as clipped
    crest_factor_min_db: float = 6.0     # below this → squashed dynamics

    # ---------- Mastering targets ----------
    target_lufs: float = -14.0           # Spotify / YouTube loudness standard
    true_peak_ceiling_dbtp: float = -1.0 # headroom so codecs don't distort
    target_sample_rate: int = 48_000

    # ---------- Compute ----------
    compute_device: str = "auto"         # "auto", "cuda", "mps" or "cpu"
    enable_audiosr: bool = True          # use AudioSR when it is installed

    # ---------- Web API ----------
    cors_origins: list[str] = ["http://localhost:3000"]   # websites allowed to call the API


@lru_cache
def get_settings() -> Settings:
    return Settings()
