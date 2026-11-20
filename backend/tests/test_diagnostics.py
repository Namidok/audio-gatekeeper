"""The DSP gatekeeper on synthetic signals with known answers."""
import numpy as np
import pytest

from app.dsp_diagnostics import (
    AudioData,
    analyze,
    detect_clipping,
    load_audio,
    measure_bandwidth,
    measure_dynamics,
    measure_loudness,
)
from app.errors import UserInputError
from tests.signals import hard_clip, lowpass, music, sine, write


# ---------- clipping ----------
def test_hard_clipped_audio_is_detected():
    x = hard_clip(music(seed=1), drive_db=9)
    result = detect_clipping(AudioData(x, 48_000))
    assert result.is_clipped
    assert result.clip_events > 10


def test_clipped_then_turned_down_is_still_detected():
    """Regression: an absolute 0.99 threshold missed this (found in the audit)."""
    x = hard_clip(music(seed=1), drive_db=9) * np.float32(0.708)   # -3 dB after clipping
    assert detect_clipping(AudioData(x, 48_000)).is_clipped


def test_loud_but_clean_audio_is_not_clipped():
    assert not detect_clipping(AudioData(music(peak_dbfs=-0.1, seed=2), 48_000)).is_clipped


@pytest.mark.parametrize("freq", [50, 1000, 8000])
def test_pure_tones_are_not_mistaken_for_clipping(freq):
    assert not detect_clipping(AudioData(sine(freq, amp=0.9), 48_000)).is_clipped


def test_quiet_audio_is_not_checked_for_clipping():
    x = hard_clip(music(seed=1), 9) * np.float32(0.05)     # peaks at -26 dBFS
    assert detect_clipping(AudioData(x, 48_000)).clip_level == 0.0


# ---------- dynamics ----------
def test_sine_crest_factor_is_3db_and_squashed():
    d = measure_dynamics(AudioData(sine(1000, amp=0.5), 48_000), min_crest_db=6.0)
    assert d.crest_factor_db == pytest.approx(3.01, abs=0.05)
    assert d.is_squashed


def test_noise_with_transients_is_not_squashed():
    assert not measure_dynamics(AudioData(music(crest=2.0), 48_000)).is_squashed


def test_silence_has_zero_crest():
    d = measure_dynamics(AudioData(np.zeros((48_000, 2), np.float32), 48_000))
    assert d.crest_factor_db == 0.0 and not d.is_squashed


# ---------- bandwidth (spectral cliff decides; 85% rolloff is reported only) ----------
@pytest.mark.slow
def test_mp3_like_cutoff_found_near_11khz():
    b = measure_bandwidth(AudioData(lowpass(music(sr=44_100), 44_100, 11_000), 44_100))
    assert 10_500 <= b.cutoff_hz <= 12_000
    assert b.is_band_limited and not b.limited_by_sample_rate


def test_full_band_audio_is_not_band_limited_even_with_low_rolloff():
    """Music with a typical spectral tilt has a low 85% rolloff; the spec's raw rule would wrongly flag it."""
    b = measure_bandwidth(AudioData(music(sr=44_100, slope=1.0), 44_100))
    assert not b.is_band_limited
    assert np.isfinite(b.rolloff_85_hz) and b.rolloff_85_hz < 15_000   # still reported


def test_low_sample_rate_is_reported_as_such():
    b = measure_bandwidth(AudioData(music(sr=22_050), 22_050))
    assert b.is_band_limited and b.limited_by_sample_rate


def test_16khz_cutoff_is_not_flagged_below_15khz_rule():
    b = measure_bandwidth(AudioData(lowpass(music(sr=44_100), 44_100, 16_000), 44_100))
    assert not b.is_band_limited


# ---------- loudness (ITU-R BS.1770) ----------
def test_bs1770_reference_997hz_sine_reads_minus_3_lufs():
    """BS.1770: a 0 dBFS 997 Hz sine in one channel measures -3.01 LUFS (tolerance 0.1 LU)."""
    loud = measure_loudness(AudioData(sine(997, seconds=5, amp=1.0, channels=1), 48_000))
    assert loud.integrated_lufs == pytest.approx(-3.01, abs=0.1)


def test_silence_has_no_loudness():
    assert measure_loudness(AudioData(np.zeros((48_000 * 2, 2), np.float32), 48_000)).integrated_lufs is None


# ---------- analyze(): score + flags ----------
def test_clean_track_bypasses_ai(tmp_path):
    r = analyze(write(tmp_path / "clean.wav", music(peak_dbfs=-1.0), 48_000))
    assert not r.flags.declip and not r.flags.upsample
    assert r.health_score >= 90 and r.grade == "A"


def test_damaged_track_gets_both_flags(tmp_path):
    x = hard_clip(lowpass(music(sr=44_100, seed=3), 44_100, 11_000), 9)
    r = analyze(write(tmp_path / "bad.wav", x, 44_100))
    assert r.flags.declip and r.flags.upsample
    assert r.health_score < 50
    assert {i.code for i in r.issues} >= {"clipping", "band_limited"}


def test_silent_track_scores_zero_and_has_no_signal(tmp_path):
    r = analyze(write(tmp_path / "silence.wav", np.zeros((48_000 * 3, 2), np.float32), 48_000))
    assert r.health_score == 0 and r.grade == "F" and not r.has_signal
    assert not r.flags.declip and not r.flags.upsample


def test_loudness_messages_follow_the_profile(tmp_path):
    path = write(tmp_path / "quiet.wav", music(peak_dbfs=-20.0), 48_000)
    spotify = analyze(path, profile_name="spotify")
    preserve = analyze(path, profile_name="preserve")
    assert any("-14 LUFS" in i.message for i in spotify.issues if i.code == "loudness")
    assert not [i for i in preserve.issues if i.code == "loudness"]


# ---------- load limits (decompression-bomb protection) ----------
def test_too_long_file_is_refused_before_decoding(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_duration_seconds", 2.0)
    with pytest.raises(UserInputError) as err:
        load_audio(write(tmp_path / "long.wav", sine(440, seconds=3), 48_000))
    assert err.value.code == "too_long"


def test_too_many_channels_is_refused(tmp_path):
    with pytest.raises(UserInputError) as err:
        load_audio(write(tmp_path / "surround.wav", sine(440, seconds=1, channels=6), 48_000))
    assert err.value.code == "too_many_channels"


def test_out_of_memory_is_not_blamed_on_the_file(tmp_path, monkeypatch):
    """MemoryError is our problem, not a damaged upload: it must reach the retry/internal path."""
    import app.dsp_diagnostics as dsp

    def no_memory(*args, **kwargs):
        raise MemoryError

    path = write(tmp_path / "ok.wav", sine(440, seconds=1), 48_000)
    monkeypatch.setattr(dsp.sf, "read", no_memory)
    with pytest.raises(MemoryError):
        load_audio(path)


def test_garbage_bytes_are_refused(tmp_path):
    path = tmp_path / "fake.wav"
    path.write_bytes(b"this is not audio" * 100)
    with pytest.raises(UserInputError) as err:
        load_audio(path)
    assert err.value.code == "unreadable"
