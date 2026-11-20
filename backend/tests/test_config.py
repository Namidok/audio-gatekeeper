"""Settings validation, loudness profiles and the Redis URL helper."""
import pytest
from pydantic import ValidationError

from app.celery_app import redis_db_url
from app.config import ENV_FILE, Settings


def test_env_file_is_absolute_and_inside_backend():
    assert ENV_FILE.is_absolute() and ENV_FILE.parent.name == "backend"


def test_compute_device_is_validated_at_startup():
    assert Settings(compute_device="CUDA").compute_device == "cuda"
    with pytest.raises(ValidationError):
        Settings(compute_device="tpu")


@pytest.mark.parametrize("field, value", [
    ("clip_threshold", 1.5), ("target_lufs", 0), ("true_peak_ceiling_dbtp", 2),
    ("max_upload_bytes", 0), ("target_sample_rate", 47_000), ("loudness_profile", "tidal"),
])
def test_out_of_range_settings_are_refused(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_profiles():
    s = Settings()
    assert s.profile("spotify").target_lufs == -14.0 and s.profile("spotify").loud_ceiling_dbtp == -2.0
    assert s.profile("preserve").target_lufs is None
    custom = Settings(target_lufs=-16, true_peak_ceiling_dbtp=-1.5).profile("custom")
    assert (custom.target_lufs, custom.ceiling_dbtp) == (-16, -1.5)
    with pytest.raises(ValueError):
        s.profile("nope")


@pytest.mark.parametrize("url, expected", [
    ("redis://localhost:6379/0", "redis://localhost:6379/1"),
    ("redis://localhost:6379", "redis://localhost:6379/1"),
    ("rediss://:p%40ss@host:6380/0?ssl_cert_reqs=required", "rediss://:p%40ss@host:6380/1?ssl_cert_reqs=required"),
    ("unix:///tmp/redis.sock?db=0", "unix:///tmp/redis.sock?db=1"),
])
def test_redis_db_url_keeps_credentials_and_tls_options(url, expected):
    assert redis_db_url(url, 1) == expected
