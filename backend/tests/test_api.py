"""FastAPI endpoints (TestClient + fakeredis + moto, Celery queue mocked)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import redis
from fastapi.testclient import TestClient

from app import main
from app.job_store import get_job_store
from app.storage import get_storage, output_key
from tests.signals import music, wav_bytes


class _Pong:
    def ping(self):
        return True


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "enqueue_job", lambda job_id, queue="default": calls.append((job_id, queue)) or "t1")
    return calls


@pytest.fixture
def client(store, storage, queued):
    main.app.dependency_overrides[get_job_store] = lambda: store
    main.app.dependency_overrides[get_storage] = lambda: storage
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _upload(client, storage, name="song.wav"):
    data = wav_bytes(music(seconds=1), 48_000)
    res = client.post("/uploads", json={"filename": name, "size_bytes": len(data)})
    assert res.status_code == 200, res.text
    body = res.json()
    storage.s3.put_object(Bucket=storage.bucket, Key=body["object_key"], Body=data,
                          ContentType=body["headers"]["Content-Type"])
    return body


def _job(client, storage, filename="a.wav"):
    up = _upload(client, storage)
    res = client.post("/jobs", json={"object_key": up["object_key"], "filename": filename})
    assert res.status_code == 202, res.text
    return up, res.json()


# ---------- health / profiles ----------
def test_health_reports_redis_and_queue(client, monkeypatch):
    monkeypatch.setattr(main.redis.Redis, "from_url", lambda *a, **k: _Pong())
    assert client.get("/health").json() == {"status": "ok", "redis": True, "queue": True, "storage": None}


def test_health_full_checks_storage(client, storage, monkeypatch):
    monkeypatch.setattr(main.redis.Redis, "from_url", lambda *a, **k: _Pong())
    monkeypatch.setattr(main, "get_storage", lambda: storage)
    assert client.get("/health?full=true").json()["storage"] is True


def test_profiles_list_has_sources(client):
    names = {p["name"]: p for p in client.get("/profiles").json()}
    assert {"spotify", "youtube", "preserve", "custom"} <= set(names)
    assert names["spotify"]["target_lufs"] == -14.0 and names["preserve"]["target_lufs"] is None


# ---------- uploads ----------
def test_upload_returns_signed_put(client):
    body = client.post("/uploads", json={"filename": "a.mp3", "size_bytes": 10}).json()
    assert body["method"] == "PUT" and body["headers"] == {"Content-Type": "audio/mpeg"}
    assert body["object_key"].startswith("uploads/") and "X-Amz-Signature" in body["upload_url"]


@pytest.mark.parametrize("payload, code", [
    ({"filename": "a.exe", "size_bytes": 10}, 415),
    ({"filename": "a.wav", "size_bytes": 10 ** 12}, 413),
    ({"filename": "a.wav", "size_bytes": 0}, 422),
    ({"filename": "", "size_bytes": 10}, 422),
])
def test_upload_rejections(client, payload, code):
    assert client.post("/uploads", json=payload).status_code == code


# ---------- jobs ----------
def test_create_job_queues_once_and_hides_internals(client, storage, queued):
    _, job = _job(client, storage, "My Song.wav")
    assert job["status"] == "queued" and job["original_filename"] == "My Song.wav"
    assert queued == [(job["job_id"], "default")]
    assert "input_ref" not in job and "input_etag" not in job
    assert (job["queue"], job["attempts"], job["max_attempts"]) == ("default", 0, 3)
    assert job["original_url"] and job["enhanced_url"] is None and job["download_url"] is None


def test_same_upload_twice_returns_the_same_job(client, storage, queued):
    up = _upload(client, storage)
    first = client.post("/jobs", json={"object_key": up["object_key"], "filename": "a.wav"}).json()
    second = client.post("/jobs", json={"object_key": up["object_key"], "filename": "a.wav"}).json()
    assert first["job_id"] == second["job_id"] and len(queued) == 1


def test_job_for_missing_upload_is_400(client):
    key = "uploads/" + "a" * 32 + "/source.wav"
    assert client.post("/jobs", json={"object_key": key, "filename": "a.wav"}).status_code == 400


@pytest.mark.parametrize("key", ["outputs/x/master.wav", "uploads/" + "a" * 32 + "/source.wav\n"])
def test_job_with_foreign_key_is_400(client, key):
    assert client.post("/jobs", json={"object_key": key, "filename": "a.wav"}).status_code == 400


def test_queue_down_gives_503_and_frees_the_upload(client, storage, store, monkeypatch):
    up = _upload(client, storage)

    def broken(*a, **k):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(main, "enqueue_job", broken)
    res = client.post("/jobs", json={"object_key": up["object_key"], "filename": "a.wav"})
    assert res.status_code == 503 and "queue" in res.json()["detail"]
    assert store.claim_upload(up["object_key"], "retry") is None      # free to try again


def test_redis_down_gives_503_with_cors(client, store, monkeypatch):
    def down(*a, **k):
        raise redis.exceptions.ConnectionError("down")

    monkeypatch.setattr(store, "get", down)
    res = client.get("/jobs/" + "a" * 32, headers={"Origin": "http://localhost:3000"})
    assert res.status_code == 503
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_get_job_404s(client):
    assert client.get("/jobs/" + "f" * 32).status_code == 404
    assert client.get("/jobs/not-a-job-id").status_code == 404


def test_completed_job_has_play_and_download_links(client, storage, store):
    _, job = _job(client, storage, "Tune.wav")
    store.update(job["job_id"], status="completed", output_ref=output_key(job["job_id"]))
    done = client.get(f"/jobs/{job['job_id']}").json()
    assert done["enhanced_url"] and done["links_expire_in"] > 0
    assert "attachment" in done["download_url"] and "Tune_mastered.wav" in done["download_url"]


def test_stalled_job_is_reported_as_failed(client, storage, store):
    _, job = _job(client, storage)
    store.set_status(job["job_id"], "mastering")
    raw = store.get(job["job_id"])
    raw["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.r.set("agk:job:" + job["job_id"], json.dumps(raw))
    body = client.get(f"/jobs/{job['job_id']}").json()
    assert body["status"] == "failed" and body["error_code"] == "stalled"


def test_delete_running_job_is_refused(client, storage):
    """A running worker would write its results back after the delete (code review finding)."""
    up, job = _job(client, storage)
    assert client.delete(f"/jobs/{job['job_id']}").status_code == 409
    assert storage.stat(up["object_key"]) is not None


def test_resubmitting_after_a_failed_job_starts_a_new_one(client, storage, store, queued):
    up, job = _job(client, storage)
    store.fail(job["job_id"], "boom", "storage_error")
    again = client.post("/jobs", json={"object_key": up["object_key"], "filename": "a.wav"}).json()
    assert again["job_id"] != job["job_id"] and again["status"] == "queued" and len(queued) == 2


def test_rate_limit_uses_the_proxy_added_address_not_the_spoofable_one(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    codes = [
        client.post("/uploads", json={"filename": "a.wav", "size_bytes": 1},
                    headers={"X-Forwarded-For": f"203.0.113.{i}, 10.0.0.9"}).status_code
        for i in range(3)          # the client varies the leftmost entry; our proxy appended 10.0.0.9
    ]
    assert codes == [200, 200, 429]


def test_delete_job_removes_files_and_record(client, storage, store):
    up, job = _job(client, storage)
    store.set_status(job["job_id"], "completed")
    assert client.delete(f"/jobs/{job['job_id']}").status_code == 204
    assert storage.stat(up["object_key"]) is None
    assert client.get(f"/jobs/{job['job_id']}").status_code == 404


# ---------- abuse protection ----------
def test_rate_limit_returns_429(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    codes = [client.post("/uploads", json={"filename": "a.wav", "size_bytes": 1}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_api_key_is_enforced_when_configured(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "s3cret")
    payload = {"filename": "a.wav", "size_bytes": 1}
    assert client.post("/uploads", json=payload).status_code == 401
    assert client.post("/uploads", json=payload, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/uploads", json=payload, headers={"X-API-Key": "s3cret"}).status_code == 200


def test_openapi_documents_typed_diagnostics(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert "Diagnostics" in schema and "Processing" in schema
    assert set(schema["JobResponse"]["properties"]["status"]["enum"]) >= {"queued", "completed", "failed"}
