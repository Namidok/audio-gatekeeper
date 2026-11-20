"""Step 14: AudioSR adapter.

Used only on a worker where AudioSR is installed. AudioSR is imported inside
the functions (never at the top of this file), so the module can still be
imported and tested on machines that don't have it, like a MacBook.
"""
import logging
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from app.ai_pipeline import resample

log = logging.getLogger(__name__)

AI_RATE = 48_000          # AudioSR always works at 48 kHz
CHUNK_SECONDS = 5.12      # AudioSR's recommended segment length
OVERLAP_SECONDS = 0.5     # chunks overlap so the joins can be crossfaded
BLEND_WIDTH_HZ = 1_000    # width of the smooth handover around the cutoff
DDIM_STEPS = 50           # AI quality vs speed (AudioSR's default is 200)
MAX_GAIN = 4.0            # never boost the AI's highs more than this


@lru_cache(maxsize=2)
def _load_model(device_type: str):
    """Load the AI model once per worker process, then reuse it for every job."""
    from audiosr import build_model
    log.info("Loading AudioSR on %s (first job only)", device_type)
    return build_model(model_name="basic", device=device_type)


def _run_model(model, chunk: np.ndarray) -> np.ndarray:
    """Send one mono 48 kHz chunk through AudioSR. AudioSR only reads from files."""
    from audiosr import super_resolution
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chunk.wav"
        sf.write(path, chunk, AI_RATE, subtype="FLOAT")
        out = super_resolution(model, str(path), seed=42,
                               ddim_steps=DDIM_STEPS, guidance_scale=3.5)
    return np.asarray(out, dtype=np.float32).reshape(-1)


def _merge_highs(original: np.ndarray, ai: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Keep the original below the cutoff and take only the AI's new highs above it."""
    n = len(original)
    ai = np.pad(ai, (0, max(0, n - len(ai))))[:n]          # AudioSR pads its output

    X = np.fft.rfft(original.astype(np.float64))           # original, as frequencies
    Y = np.fft.rfft(ai.astype(np.float64))                 # AI output, as frequencies
    freqs = np.fft.rfftfreq(n, 1 / AI_RATE)

    # 1. Match levels: AudioSR rescales its output, so compare a band both versions share
    low_edge = cutoff_hz - BLEND_WIDTH_HZ / 2
    shared = (freqs >= 0.5 * cutoff_hz) & (freqs < low_edge)
    ai_level = np.abs(Y[shared]).sum()
    gain = np.abs(X[shared]).sum() / ai_level if ai_level > 0 else 0.0
    gain = min(gain, MAX_GAIN)

    # 2. Crossover: weight goes smoothly from 0 (all original) to 1 (all AI)
    ramp = np.clip((freqs - low_edge) / BLEND_WIDTH_HZ, 0.0, 1.0)
    weight = 0.5 - 0.5 * np.cos(np.pi * ramp)
    merged = X * (1.0 - weight) + gain * Y * weight
    return np.fft.irfft(merged, n).astype(np.float32)


def _process_channel(model, channel: np.ndarray, cutoff_hz: float, label: str) -> np.ndarray:
    n = len(channel)
    size = int(CHUNK_SECONDS * AI_RATE)
    overlap = int(OVERLAP_SECONDS * AI_RATE)
    hop = size - overlap
    starts = list(range(0, max(n - overlap, 1), hop))

    total = np.zeros(n, dtype=np.float64)
    weight = np.zeros(n, dtype=np.float64)

    for i, start in enumerate(starts, 1):
        end = min(start + size, n)
        chunk = channel[start:end]

        too_short = len(chunk) < AI_RATE // 10
        silent = np.sqrt(np.mean(chunk.astype(np.float64) ** 2)) < 1e-5
        if too_short or silent:
            fixed = chunk.astype(np.float64)                  # nothing for the AI to do
        else:
            log.info("AudioSR %s: chunk %d/%d", label, i, len(starts))
            fixed = _merge_highs(chunk, _run_model(model, chunk), cutoff_hz)

        # Fade in/out over the overlap so neighbouring chunks blend without clicks
        window = np.ones(len(chunk))
        fade = min(overlap, len(chunk))
        if start > 0:
            window[:fade] = np.linspace(0.0, 1.0, fade)
        if end < n:
            window[-fade:] = np.linspace(1.0, 0.0, fade)

        total[start:end] += fixed * window
        weight[start:end] += window

    return (total / np.maximum(weight, 1e-12)).astype(np.float32)


def extend_with_audiosr(samples: np.ndarray, orig_sr: int, cutoff_hz: float,
                        device: torch.device, target_sr: int = 48_000) -> np.ndarray:
    if target_sr != AI_RATE:
        raise ValueError(f"AudioSR only produces {AI_RATE} Hz audio, not {target_sr} Hz")

    model = _load_model(device.type)
    base = resample(samples, orig_sr, AI_RATE, device)     # bring it to 48 kHz first

    # Run the AI once, on the mid (L+R)/2 signal, and add only the NEW highs it generates to every
    # channel. Running left and right separately would generate different, uncorrelated highs per
    # side (a smeared stereo image) and cost twice the GPU time. Below the cutoff, every channel
    # keeps its original samples, so the stereo image there is untouched.
    mid = base.mean(axis=1)
    new_highs = _process_channel(model, mid, cutoff_hz, label="mid") - mid
    return (base + new_highs[:, None]).astype(np.float32)
