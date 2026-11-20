"""De-clipping and resampling."""
import numpy as np
import pytest

from app.ai_pipeline import declip, pick_device, resample
from tests.signals import lowpass, music, sine


def _chord(seconds=3.0, sr=48_000):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.6 * np.sin(2 * np.pi * 55 * t) + 0.3 * np.sin(2 * np.pi * 220 * t + 1)
         + 0.2 * np.sin(2 * np.pi * 330 * t + 2) + 0.1 * np.sin(2 * np.pi * 660 * t))
    return np.stack([x, np.roll(x, 37)], axis=1)


# ---------- de-clip ----------
def test_declip_reduces_error_on_tonal_peaks():
    """Measured in the audit: about -14 dB error on bass/chord peaks clipped by 3 dB."""
    chord = _chord()
    truth = chord / np.abs(chord).max() * 10 ** (3 / 20)
    clipped = np.clip(truth, -1, 1).astype(np.float32)
    fixed, repaired = declip(clipped)
    mask = np.abs(truth) > 1
    before = np.sqrt(np.mean((clipped[mask] - truth[mask]) ** 2))
    after = np.sqrt(np.mean((fixed[mask] - truth[mask]) ** 2))
    assert repaired > 0
    assert 20 * np.log10(after / before) < -6


def test_declip_leaves_unclipped_audio_alone():
    x = sine(440, amp=0.8)
    fixed, repaired = declip(x)
    assert repaired == 0
    np.testing.assert_array_equal(fixed, x)


def test_declip_keeps_sign_and_bounds_the_boost():
    chord = _chord()
    truth = chord / np.abs(chord).max() * 2.0
    clipped = np.clip(truth, -1, 1).astype(np.float32)
    fixed, _ = declip(clipped)
    flat = np.abs(clipped) >= 0.99
    assert np.all(np.sign(fixed[flat]) == np.sign(clipped[flat]))
    assert np.all(np.abs(fixed[flat]) >= np.abs(clipped[flat]) - 1e-6)   # never below the flat top
    assert np.abs(fixed).max() <= 4.0 + 1e-6            # safety cap: at most +12 dB above the flat top


def test_declip_improves_music_like_material_at_light_clipping():
    """Regression: the earlier cubic spline made real music WORSE at light clipping (+3 dB)."""
    truth = music(seconds=10, sr=44_100, peak_dbfs=-1.0, seed=1, slope=0.5, crest=0.5).astype(np.float64)
    truth *= 10 ** (6 / 20)
    clipped = np.clip(truth, -1, 1).astype(np.float32)
    fixed, repaired = declip(clipped)
    destroyed = np.abs(truth) > 1
    before = np.sqrt(np.mean((clipped[destroyed] - truth[destroyed]) ** 2))
    after = np.sqrt(np.mean((fixed[destroyed] - truth[destroyed]) ** 2))
    assert repaired > 0 and 20 * np.log10(after / before) < -1.0
    untouched = ~(np.abs(clipped) >= 0.99 * np.abs(clipped).max())
    np.testing.assert_array_equal(fixed[untouched], clipped[untouched])   # unclipped samples never change


# ---------- resample ----------
def test_resample_44k1_to_48k_keeps_length_pitch_and_level():
    device = pick_device("cpu")
    x = sine(1000, seconds=2, sr=44_100, amp=0.5)
    y = resample(x, 44_100, 48_000, device)
    assert y.shape == (96_000, 2)
    spectrum = np.abs(np.fft.rfft(y[:, 0] * np.hanning(len(y))))
    peak_hz = np.fft.rfftfreq(len(y), 1 / 48_000)[np.argmax(spectrum)]
    assert peak_hz == pytest.approx(1000, abs=2)
    assert np.abs(y[4800:-4800, 0]).max() == pytest.approx(0.5, abs=0.005)


def test_resample_keeps_peaks_above_full_scale():
    y = resample(sine(500, seconds=1, sr=44_100, amp=1.5), 44_100, 48_000, pick_device("cpu"))
    assert np.abs(y).max() > 1.4


def test_same_rate_is_a_passthrough():
    x = sine(440, seconds=1)
    assert resample(x, 48_000, 48_000, pick_device("cpu")) is x


def test_resampling_does_not_invent_missing_highs():
    x = lowpass(music(seconds=3, sr=44_100), 44_100, 11_000)
    y = resample(x, 44_100, 48_000, pick_device("cpu"))
    spec = np.abs(np.fft.rfft(y[:, 0]))
    freqs = np.fft.rfftfreq(len(y), 1 / 48_000)
    above = spec[freqs > 12_000].mean()
    below = spec[(freqs > 5_000) & (freqs < 10_000)].mean()
    assert above < below * 1e-3                          # still nothing up there
