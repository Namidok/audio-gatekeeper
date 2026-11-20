"""Redis job store (fakeredis)."""
import json
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app import job_store as js
from app.job_store import JobStore, is_stalled


def test_create_sets_defaults_and_ttl(store, settings):
    job = store.create("song.mp3", "uploads/x/source.mp3", input_etag="abc", loudness_profile="youtube")
    assert job["status"] == "queued" and job["progress"] == 0 and job["attempts"] == 0
    assert job["loudness_profile"] == "youtube" and job["input_etag"] == "abc"
    ttl = store.r.ttl(js.KEY_PREFIX + job["job_id"])
    assert settings.job_ttl_seconds - 5 < ttl <= settings.job_ttl_seconds


def test_status_sets_progress(store):
    job = store.create("a.wav", "k")
    assert store.set_status(job["job_id"], "mastering")["progress"] == js.PROGRESS["mastering"]


def test_unknown_status_and_unknown_field_are_refused(store):
    job = store.create("a.wav", "k")
    with pytest.raises(ValueError):
        store.set_status(job["job_id"], "exploding")
    with pytest.raises(KeyError):
        store.update(job["job_id"], ouput_ref="typo")


def test_update_missing_job_raises(store):
    with pytest.raises(KeyError):
        store.update("0" * 32, status="queued")


@pytest.mark.parametrize("final", ["completed", "failed"])
def test_finished_job_stays_finished(store, final):
    job = store.create("a.wav", "k")
    store.set_status(job["job_id"], final)
    assert store.set_status(job["job_id"], "queued")["status"] == final


def test_finished_job_still_accepts_non_status_fields(store):
    job = store.create("a.wav", "k")
    store.set_status(job["job_id"], "completed")
    assert store.update(job["job_id"], waveforms={"original": [], "enhanced": []})["waveforms"] is not None


def test_ttl_is_kept_on_update(store):
    job = store.create("a.wav", "k")
    key = js.KEY_PREFIX + job["job_id"]
    store.r.expire(key, 1234)
    store.set_status(job["job_id"], "diagnosing")
    assert 1200 < store.r.ttl(key) <= 1234


def test_concurrent_write_is_retried_and_both_changes_survive(monkeypatch):
    """Optimistic locking: another process writes between our read and our save."""
    server = fakeredis.FakeServer()
    store = JobStore(fakeredis.FakeRedis(server=server, decode_responses=True))
    other = fakeredis.FakeRedis(server=server, decode_responses=True)   # a second process
    job = store.create("a.wav", "k")
    key = js.KEY_PREFIX + job["job_id"]
    real_loads = json.loads
    calls = {"n": 0}

    def interfering_loads(raw):
        calls["n"] += 1
        if calls["n"] == 1:   # first read: someone else updates the message meanwhile
            data = real_loads(raw)
            data["message"] = "written by another process"
            other.set(key, json.dumps(data), keepttl=True)
        return real_loads(raw)

    monkeypatch.setattr(js.json, "loads", interfering_loads)
    result = store.set_status(job["job_id"], "diagnosing", "Checking")
    monkeypatch.undo()
    assert calls["n"] >= 2                                  # it had to read again
    assert result["status"] == "diagnosing"
    saved = store.get(job["job_id"])
    assert saved["status"] == "diagnosing" and saved["message"] == "Checking"  # our write won, after re-reading


def test_fail_truncates_and_records_code(store):
    job = store.create("a.wav", "k")
    failed = store.fail(job["job_id"], "x" * 5000, "internal_error", "ref123")
    assert len(failed["error"]) == 2000 and failed["error_code"] == "internal_error"
    assert failed["error_ref"] == "ref123" and failed["status"] == "failed"


def test_attempts_are_counted(store):
    job = store.create("a.wav", "k")
    assert store.begin_attempt(job["job_id"]) == 1
    assert store.begin_attempt(job["job_id"]) == 2


def test_upload_can_only_be_claimed_once(store):
    assert store.claim_upload("uploads/a/source.wav", "job1") is None
    assert store.claim_upload("uploads/a/source.wav", "job2") == "job1"
    store.release_upload("uploads/a/source.wav")
    assert store.claim_upload("uploads/a/source.wav", "job3") is None


def test_stalled_detection(settings):
    old = (datetime.now(timezone.utc) - timedelta(seconds=settings.job_time_limit_seconds + 400)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    assert is_stalled({"status": "mastering", "updated_at": old})
    assert not is_stalled({"status": "mastering", "updated_at": fresh})
    assert not is_stalled({"status": "completed", "updated_at": old})
    assert not is_stalled({"status": "queued", "attempts": 0, "updated_at": old})   # just a long queue


def test_store_uses_injected_client():
    client = fakeredis.FakeRedis(decode_responses=True)
    assert JobStore(client).r is client
