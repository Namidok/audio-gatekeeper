"""Waveform peaks: a small summary of a song's shape for the dashboard to draw.

A 3-minute song has ~17 million samples; the browser only needs ~800 numbers to draw it.
"""
from pathlib import Path

import numpy as np

from app.dsp_diagnostics import AudioData, load_audio


def waveform_peaks(source: str | Path | AudioData | np.ndarray, buckets: int = 800) -> list[float]:
    """Split the song into `buckets` equal slices and keep the loudest point of each (0.0 to 1.0).

    Accepts a file path, or audio that is already in memory (so it isn't decoded again).
    """
    if isinstance(source, AudioData):
        samples = source.samples
    elif isinstance(source, np.ndarray):
        samples = source if source.ndim == 2 else source[:, None]
    else:
        samples = load_audio(source).samples
    loudest = np.abs(samples).max(axis=1)          # loudest channel at each moment
    buckets = max(1, min(buckets, len(loudest)))
    slices = np.array_split(loudest, buckets)
    return [round(float(min(s.max(), 1.0)), 3) for s in slices]
