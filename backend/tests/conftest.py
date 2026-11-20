"""Shared fixtures. The suite never touches real AWS, a real Redis server, a GPU or backend/.env."""
import os

# --- 1. Isolate configuration BEFORE any app module creates its settings ----------------------
_TEST_ENV = {
    "S3_BUCKET": "test-bucket",
    "S3_REGION": "us-east-1",
    "S3_ACCESS_KEY_ID": "testing",
    "S3_SECRET_ACCESS_KEY": "testing",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    "REDIS_URL": "redis://localhost:6399/0",   # never used: every test injects fakeredis
    "COMPUTE_DEVICE": "cpu",
    "ENABLE_AUDIOSR": "false",
    "GPU_QUEUE_ENABLED": "false",
    "RATE_LIMIT_PER_MINUTE": "1000",
    "LOUDNESS_PROFILE": "spotify",
}
for _name in ("S3_ENDPOINT_URL", "AWS_PROFILE", "AWS_SESSION_TOKEN", "API_KEY"):
    os.environ.pop(_name, None)
os.environ.update(_TEST_ENV)

from app import config  # noqa: E402

config.Settings.model_config["env_file"] = None   # do not read backend/.env
config.get_settings.cache_clear()

import boto3  # noqa: E402
import fakeredis  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.job_store import JobStore  # noqa: E402
from app.storage import Storage  # noqa: E402


@pytest.fixture
def settings():
    return config.get_settings()


@pytest.fixture
def store():
    return JobStore(fakeredis.FakeRedis(decode_responses=True))


@pytest.fixture
def s3():
    """An in-memory S3 with the test bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        yield client


@pytest.fixture
def storage(s3):
    return Storage()   # the real constructor; moto intercepts its calls


def put_object(storage: Storage, key: str, data: bytes, content_type: str = "audio/wav") -> None:
    storage.s3.put_object(Bucket=storage.bucket, Key=key, Body=data, ContentType=content_type)
