"""Synthetic, reproducible test signals (no copyrighted audio, no files in the repo)."""
import io

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly


def sine(freq: float, seconds: float = 3.0, sr: int = 48_000, amp: float = 0.5,
         channels: int = 2, phase: float = 0.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    x = amp * np.sin(2 * np.pi * freq * t + phase)
    return np.repeat(x[:, None], channels, axis=1).astype(np.float32)


def music(seconds: float = 6.0, sr: int = 48_000, peak_dbfs: float = -1.0, crest: float = 1.0,
          seed: int = 0, slope: float = 0.5) -> np.ndarray:
    """Stereo noise with drum-like transients: a crude, deterministic stand-in for music.

    slope 0.5 = pink noise (-3 dB/octave); real mixes usually fall off faster (~0.75-1.0).
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    spec = np.fft.rfft(rng.standard_normal((n, 2)), axis=0)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spec[1:] /= freqs[1:, None] ** slope
    spec[0] = 0
    y = np.fft.irfft(spec, n, axis=0)
    env = np.zeros(n)
    for k in range(0, n, sr // 2):
        m = min(3000, n - k)
        env[k:k + m] += np.exp(-np.arange(m) / 400) * crest
    y *= (1 + env)[:, None]
    return (y / np.abs(y).max() * 10 ** (peak_dbfs / 20)).astype(np.float32)


def lowpass(x: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Brick-wall low-pass, like an MP3 encoder's cutoff."""
    spec = np.fft.rfft(x, axis=0)
    spec[np.fft.rfftfreq(x.shape[0], 1 / sr) > cutoff_hz] = 0
    return np.fft.irfft(spec, x.shape[0], axis=0).astype(np.float32)


def hard_clip(x: np.ndarray, drive_db: float) -> np.ndarray:
    return np.clip(x * 10 ** (drive_db / 20), -1.0, 1.0).astype(np.float32)


def wav_bytes(x: np.ndarray, sr: int, subtype: str = "PCM_16") -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype=subtype)
    return buf.getvalue()


def write(path, x: np.ndarray, sr: int, subtype: str = "FLOAT"):
    sf.write(str(path), x, sr, subtype=subtype)
    return path


def lufs(x: np.ndarray, sr: int) -> float:
    return float(pyln.Meter(sr).integrated_loudness(x.astype(np.float64)))


def independent_true_peak(x: np.ndarray, factor: int = 32) -> float:
    """dBTP measured independently of the pipeline: 32x oversampling with a long, steep filter."""
    up = resample_poly(x.astype(np.float64), factor, 1, axis=0, window=("kaiser", 16.0))
    return float(20 * np.log10(np.abs(up).max()))
