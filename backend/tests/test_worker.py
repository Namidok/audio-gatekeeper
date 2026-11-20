"""The Celery task, run eagerly (no broker): happy path, bad input, retries, crashes."""
import os

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app import worker
from app.storage import StorageError, output_key, preview_key
from tests.signals import hard_clip, lowpass, music, wav_bytes

KEY = "uploads/" + "b" * 32 + "/source.wav"


@pytest.fixture
def env(store, storage, monkeypatch):
    monkeypatch.setattr(worker, "get_job_store", lambda: store)
    monkeypatch.setattr(worker, "get_storage", lambda: storage)
    return store, storage


def _submit(store, storage, data, key=KEY):
    storage.s3.put_object(Bucket=storage.bucket, Key=key, Body=data)
    return store.create("song.wav", key, input_etag=storage.stat(key).etag)["job_id"]


def _run(job_id):
    return worker.process_job.apply(args=[job_id]).get()


def _raise(exc):
    def fn(*args, **kwargs):
        raise exc
    return fn


@pytest.mark.slow
def test_happy_path_completes_and_uploads_everything(env):
    store, storage = env
    job_id = _submit(store, storage, wav_bytes(hard_clip(music(seconds=3, sr=44_100), 6), 44_100))
    assert _run(job_id)["status"] == "completed"
    job = store.get(job_id)
    assert job["status"] == "completed" and job["progress"] == 100
    assert job["processing"]["outcome"] == "repaired"
    assert "output_path" not in job["processing"]
    assert len(job["waveforms"]["original"]) == len(job["waveforms"]["enhanced"]) == 800
    for key in (output_key(job_id), preview_key(job_id, "original"), preview_key(job_id, "enhanced")):
        assert storage.stat(key) is not None
    assert job["attempts"] == 1


def test_invalid_reference_fails_without_retry(env):
    store, _ = env
    job_id = store.create("a.wav", "outputs/someone-else/master.wav")["job_id"]
    _run(job_id)
    job = store.get(job_id)
    assert job["status"] == "failed" and job["error_code"] == "invalid_reference" and job["attempts"] == 1


def test_missing_upload_fails_with_clear_message(env):
    store, _ = env
    job_id = store.create("a.wav", KEY)["job_id"]
    _run(job_id)
    assert store.get(job_id)["error_code"] == "upload_missing"


def test_oversized_upload_fails(env, settings, monkeypatch):
    store, storage = env
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    job_id = _submit(store, storage, b"x" * 500)
    _run(job_id)
    assert store.get(job_id)["error_code"] == "too_large"


def test_undecodable_file_fails_as_user_error(env):
    store, storage = env
    job_id = _submit(store, storage, b"definitely not audio" * 50)
    _run(job_id)
    job = store.get(job_id)
    assert job["status"] == "failed" and job["error_code"] == "unreadable" and job["attempts"] == 1


def test_upload_replaced_after_submission_is_refused(env):
    store, storage = env
    job_id = _submit(store, storage, wav_bytes(music(seconds=1), 48_000))
    storage.s3.put_object(Bucket=storage.bucket, Key=KEY, Body=wav_bytes(music(seconds=1, seed=9), 48_000))
    _run(job_id)
    assert store.get(job_id)["error_code"] == "upload_changed"


@pytest.mark.slow
def test_transient_error_is_retried_then_succeeds(env, monkeypatch):
    store, storage = env
    real_enhance = worker.enhance
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary glitch")
        return real_enhance(*args, **kwargs)

    monkeypatch.setattr(worker, "enhance", flaky)
    job_id = _submit(store, storage, wav_bytes(music(seconds=2), 48_000))
    _run(job_id)
    job = store.get(job_id)
    assert job["status"] == "completed" and calls["n"] == 2 and job["attempts"] == 2


def test_retries_exhausted_fails_with_generic_message_and_ref(env, monkeypatch):
    store, storage = env
    secret_detail = "/tmp/agk-secret/path bucket=test-bucket"
    monkeypatch.setattr(worker, "enhance", _raise(RuntimeError(secret_detail)))
    job_id = _submit(store, storage, wav_bytes(music(seconds=1), 48_000))
    _run(job_id)
    job = store.get(job_id)
    assert job["status"] == "failed" and job["error_code"] == "internal_error"
    assert job["error_ref"] and secret_detail not in job["error"]          # nothing internal leaks
    assert job["attempts"] == 3                                            # 1 try + 2 retries


def test_permanent_storage_error_is_not_retried(env, monkeypatch):
    store, storage = env
    monkeypatch.setattr(worker, "enhance", _raise(StorageError("AccessDenied on bucket test-bucket", transient=False)))
    job_id = _submit(store, storage, wav_bytes(music(seconds=1), 48_000))
    _run(job_id)
    job = store.get(job_id)
    assert job["error_code"] == "storage_error" and job["attempts"] == 1
    assert "test-bucket" not in job["error"]


def test_job_that_keeps_crashing_the_worker_is_set_aside(env, settings):
    store, _ = env
    job_id = store.create("a.wav", KEY)["job_id"]
    store.update(job_id, attempts=settings.job_max_attempts)   # it already crashed the worker N times
    _run(job_id)
    assert store.get(job_id)["error_code"] == "too_many_attempts"


def test_missing_and_finished_jobs_are_noops(env):
    store, _ = env
    assert _run("c" * 32)["status"] == "missing"
    job_id = store.create("a.wav", KEY)["job_id"]
    store.set_status(job_id, "completed")
    assert _run(job_id)["status"] == "completed" and store.get(job_id)["attempts"] == 0


def test_soft_time_limit_marks_failed(env, monkeypatch):
    store, storage = env
    monkeypatch.setattr(worker, "enhance", _raise(SoftTimeLimitExceeded()))
    job_id = _submit(store, storage, wav_bytes(music(seconds=1), 48_000))
    _run(job_id)
    assert store.get(job_id)["error_code"] == "timeout"


def test_redis_down_while_failing_does_not_crash_the_task(env, monkeypatch):
    store, _ = env
    job_id = store.create("a.wav", "outputs/bad")["job_id"]
    monkeypatch.setattr(store, "fail", _raise(ConnectionError("redis down")))
    assert _run(job_id)["status"] == "failed"     # logged; the API's stall check reports it later


@pytest.mark.slow
def test_waveform_failure_does_not_fail_the_job(env, monkeypatch):
    store, storage = env
    monkeypatch.setattr(worker, "waveform_peaks", _raise(ValueError("boom")))
    job_id = _submit(store, storage, wav_bytes(music(seconds=2), 48_000))
    _run(job_id)
    job = store.get(job_id)
    assert job["status"] == "completed" and job["waveforms"] is None


def test_gpu_routing_hands_over_band_limited_jobs(env, settings, monkeypatch):
    store, storage = env
    monkeypatch.setattr(settings, "gpu_queue_enabled", True)
    sent = []
    monkeypatch.setattr(worker, "enqueue_job", lambda job_id, queue="default": sent.append(queue))
    job_id = _submit(store, storage, wav_bytes(lowpass(music(seconds=2, sr=44_100), 44_100, 11_000), 44_100))
    assert _run(job_id)["status"] == "routed_to_gpu"
    routed = store.get(job_id)
    assert sent == ["gpu"] and routed["status"] == "queued" and routed["queue"] == "gpu"
    assert routed["attempts"] == 0          # the hand-over doesn't use up one of the GPU worker's attempts


def test_workdir_is_cleaned_up_after_failure(env, monkeypatch, tmp_path):
    store, _ = env
    made = []
    real_mkdtemp = worker.tempfile.mkdtemp

    def tracking_mkdtemp(**kwargs):
        made.append(real_mkdtemp(dir=tmp_path, **kwargs))
        return made[-1]

    monkeypatch.setattr(worker.tempfile, "mkdtemp", tracking_mkdtemp)
    _run(store.create("a.wav", "outputs/bad")["job_id"])
    assert made and not any(os.path.exists(p) for p in made)
