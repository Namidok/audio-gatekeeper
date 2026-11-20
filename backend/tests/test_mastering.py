"""Mastering targets, measured independently of the pipeline's own meters."""
import numpy as np
import pytest
import soundfile as sf

from app.ai_pipeline import export_preview, export_wav, master, measure_true_peak
from tests.signals import independent_true_peak, lufs, music, sine

SR = 48_000

CASES = {
    "quiet": lambda: music(seconds=8, peak_dbfs=-12.0, seed=10),
    "typical": lambda: music(seconds=8, peak_dbfs=-0.1, seed=11),
    "loud_dense": lambda: np.clip(music(seconds=8, peak_dbfs=4.0, crest=0.3, seed=12), -0.99, 0.99).astype(np.float32),
    "bright": lambda: music(seconds=8, peak_dbfs=-0.1, seed=13) + sine(15_000, seconds=8, amp=0.05),
}


@pytest.mark.slow
@pytest.mark.parametrize("name", CASES)
def test_spotify_master_hits_minus_14_lufs_and_minus_1_dbtp(name):
    x = CASES[name]()
    m = master(x, SR, target_lufs=-14.0, ceiling_dbtp=-1.0)
    assert lufs(m.samples, SR) == pytest.approx(-14.0, abs=0.5)
    assert independent_true_peak(m.samples) <= -1.0
    assert m.true_peak_dbtp <= -1.0


@pytest.mark.slow
def test_reported_true_peak_matches_independent_meter():
    m = master(CASES["typical"](), SR, -14.0, -1.0)
    assert m.true_peak_dbtp == pytest.approx(independent_true_peak(m.samples), abs=0.1)


def test_true_peak_meter_sees_intersample_peaks():
    """fs/4 sine at 45 degrees: samples peak at 0 dBFS, the real wave at +3.01 dBTP."""
    n = np.arange(SR)
    x = np.sin(2 * np.pi * (SR / 4) * n / SR + np.pi / 4)
    fade = np.minimum(1.0, np.minimum(n, n[::-1]) / 480)         # 10 ms fades: no step at the edges
    x = (x / np.abs(x).max() * fade)[:, None].astype(np.float32)
    assert measure_true_peak(x) == pytest.approx(3.01, abs=0.05)


@pytest.mark.slow
def test_very_peaky_track_backs_off_instead_of_crushing():
    x = music(seconds=8, peak_dbfs=-0.1, crest=4.0, seed=14)
    m = master(x, SR, -14.0, -1.0, max_limit_db=6.0)
    assert m.limiter_reduction_db <= 6.0 + 1e-6
    assert m.output_lufs < -14.5                         # quieter, by design
    assert independent_true_peak(m.samples) <= -1.0


@pytest.mark.slow
def test_preserve_profile_keeps_loudness_and_applies_loud_ceiling():
    x = np.clip(music(seconds=8, peak_dbfs=4.0, crest=0.3, seed=15), -0.99, 0.99).astype(np.float32)
    before = lufs(x, SR)
    assert before > -14                                   # a loud master
    m = master(x, SR, target_lufs=None, ceiling_dbtp=-1.0, loud_ceiling_dbtp=-2.0)
    assert m.ceiling_dbtp == -2.0                         # Spotify: louder than -14 LUFS -> -2 dBTP
    assert independent_true_peak(m.samples) <= -2.0
    after = lufs(m.samples, SR)
    assert before - 3.0 <= after <= before        # only the limiting needed for the ceiling costs loudness


def test_silence_passes_through_untouched():
    x = np.zeros((SR * 2, 2), np.float32)
    m = master(x, SR)
    assert m.output_lufs is None and m.gain_db == 0.0
    np.testing.assert_array_equal(m.samples, x)


def test_shorter_than_one_loudness_block_is_left_alone():
    assert master(sine(440, seconds=0.2), SR).input_lufs is None


def test_export_is_24_bit_wav_at_the_given_rate(tmp_path):
    x = music(seconds=2, peak_dbfs=-1.0)
    path = export_wav(x, SR, tmp_path / "out.wav")
    info = sf.info(str(path))
    assert (info.format, info.subtype, info.samplerate, info.channels) == ("WAV", "PCM_24", SR, 2)
    back, _ = sf.read(str(path), dtype="float64", always_2d=True)
    assert np.abs(back - x).max() < 2 ** -22               # 24-bit precision


def test_export_clips_overs_instead_of_wrapping(tmp_path):
    path = export_wav(sine(440, seconds=1, amp=1.5), SR, tmp_path / "over.wav")
    back, _ = sf.read(str(path), always_2d=True)
    assert np.abs(back).max() <= 1.0


def test_preview_is_lossless_24_bit_flac(tmp_path):
    info = sf.info(str(export_preview(music(seconds=1, peak_dbfs=-1.0), SR, tmp_path / "p.flac")))
    assert (info.format, info.subtype) == ("FLAC", "PCM_24")
