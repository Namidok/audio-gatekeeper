"""enhance() end to end, and the honest reporting of what was (not) done."""
import types
from unittest.mock import patch

import numpy as np
import pytest

from app import ai_pipeline
from app.ai_pipeline import bandwidth_extend, enhance, pick_device
from tests.signals import hard_clip, independent_true_peak, lowpass, lufs, music, write


@pytest.mark.slow
def test_clean_track_bypasses_ai_and_is_mastered(tmp_path):
    src = write(tmp_path / "clean.wav", music(sr=44_100, peak_dbfs=-1.0), 44_100)
    r = enhance(src, tmp_path / "out.wav")
    assert r.ai_bypassed and r.outcome == "bypassed" and r.upsample_method == "none"
    assert [s.kind for s in r.steps if s.status == "applied"] == ["resample", "master"]
    assert r.output_sample_rate == 48_000
    assert lufs(r.mastered, 48_000) == pytest.approx(-14.0, abs=0.5)
    assert independent_true_peak(r.mastered) <= -1.0


@pytest.mark.slow
def test_band_limited_track_without_audiosr_is_reported_as_partial(tmp_path):
    src = write(tmp_path / "mp3ish.wav", lowpass(music(sr=44_100), 44_100, 11_000), 44_100)
    r = enhance(src, tmp_path / "out.wav")
    assert r.outcome == "partial"
    assert r.upsample_method == "resample_only" and not r.highs_restored
    assert any("not restored" in n for n in r.notes)
    step = next(s for s in r.steps if s.kind == "bandwidth_extension")
    assert step.status == "fallback" and "without restoring" in step.summary
    assert r.output_metrics.cutoff_hz < 13_000          # honestly: still band-limited


@pytest.mark.slow
def test_clipped_track_is_declipped(tmp_path):
    src = write(tmp_path / "clipped.wav", hard_clip(music(seed=4), 9), 48_000)
    r = enhance(src, tmp_path / "out.wav")
    assert r.outcome == "repaired"
    assert next(s for s in r.steps if s.kind == "declip").status == "applied"
    assert r.output_metrics.clipped_ratio < 0.0001


def test_silent_track_is_no_signal_not_healthy(tmp_path):
    src = write(tmp_path / "silence.wav", np.zeros((48_000 * 2, 2), np.float32), 48_000)
    r = enhance(src, tmp_path / "out.wav")
    assert r.outcome == "no_signal"
    assert next(s for s in r.steps if s.kind == "master").status == "skipped"


def test_public_dict_has_no_server_paths_or_audio(tmp_path):
    src = write(tmp_path / "s.wav", music(seconds=2), 48_000)
    data = enhance(src, tmp_path / "out.wav").to_public_dict()
    assert "output_path" not in data and "mastered" not in data
    assert str(tmp_path) not in repr(data)


def _fake_adapter(fn):
    module = types.ModuleType("app.audiosr_adapter")
    module.extend_with_audiosr = fn
    return module


def test_audiosr_crash_falls_back_and_says_so(settings, monkeypatch):
    monkeypatch.setattr(settings, "enable_audiosr", True)
    monkeypatch.setattr(settings, "allow_audiosr_without_cuda", True)

    def crash(*args, **kwargs):
        raise RuntimeError("CUDA out of memory (simulated)")

    with patch.object(ai_pipeline, "audiosr_import_error", return_value=None), \
         patch.dict("sys.modules", {"app.audiosr_adapter": _fake_adapter(crash)}):
        r = bandwidth_extend(music(seconds=1, sr=44_100), 44_100, 11_000, pick_device("cpu"))
    assert r.method == "resample_only" and "RuntimeError" in r.note and r.sample_rate == 48_000


def test_audiosr_is_not_used_without_cuda_by_default(settings, monkeypatch):
    monkeypatch.setattr(settings, "enable_audiosr", True)
    called = []
    with patch.object(ai_pipeline, "audiosr_import_error", return_value=None), \
         patch.dict("sys.modules", {"app.audiosr_adapter": _fake_adapter(lambda *a, **k: called.append(1))}):
        r = bandwidth_extend(music(seconds=1, sr=44_100), 44_100, 11_000, pick_device("cpu"))
    assert not called and "CUDA" in r.note


def test_audiosr_success_is_reported_as_restored(settings, monkeypatch):
    monkeypatch.setattr(settings, "enable_audiosr", True)
    monkeypatch.setattr(settings, "allow_audiosr_without_cuda", True)
    fake = _fake_adapter(lambda samples, sr, cutoff, device, target_sr: np.zeros((48_000, 2), np.float32))
    with patch.object(ai_pipeline, "audiosr_import_error", return_value=None), \
         patch.dict("sys.modules", {"app.audiosr_adapter": fake}):
        r = bandwidth_extend(music(seconds=1, sr=44_100), 44_100, 11_000, pick_device("cpu"))
    assert r.method == "audiosr" and "rebuilt" in r.note


def test_audiosr_adapter_adds_identical_new_highs_to_both_channels(monkeypatch):
    """Mid-only AI keeps the stereo image below the cutoff exactly (fake model, no AudioSR needed)."""
    from app import audiosr_adapter

    def fake_model_output(model, chunk):
        noise = np.random.default_rng(0).standard_normal(len(chunk)).astype(np.float32) * 0.01
        return chunk + noise                                   # "AI" adds broadband detail

    monkeypatch.setattr(audiosr_adapter, "_load_model", lambda device_type: object())
    monkeypatch.setattr(audiosr_adapter, "_run_model", fake_model_output)
    x = lowpass(music(seconds=2, sr=48_000), 48_000, 8_000)
    out = audiosr_adapter.extend_with_audiosr(x, 48_000, 8_000, pick_device("cpu"), 48_000)
    added = out - x
    np.testing.assert_allclose(added[:, 0], added[:, 1], atol=1e-6)    # same highs in L and R
    assert np.abs(lowpass(added, 48_000, 7_000)).max() < 1e-3          # lows untouched
