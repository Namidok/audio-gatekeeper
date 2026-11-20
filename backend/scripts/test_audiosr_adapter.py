import logging
from unittest.mock import patch

import numpy as np

from app import audiosr_adapter
from app.ai_pipeline import pick_device, resample
from app.dsp_diagnostics import AudioData, load_audio, measure_bandwidth

logging.basicConfig(level=logging.WARNING)
device = pick_device()
SR = audiosr_adapter.AI_RATE
calls = {"count": 0}


def fake_audiosr(model, chunk):
    """Behaves like AudioSR: invents highs, rescales to peak 0.5, pads to 5.12 s."""
    calls["count"] += 1
    X = np.fft.rfft(chunk.astype(np.float64))
    freqs = np.fft.rfftfreq(len(chunk), 1 / SR)
    source = (freqs >= 4_000) & (freqs < 8_000)             # copy the 4-8 kHz band...
    level = 0.5
    for shift in (4_000, 8_000, 12_000, 16_000):            # ...up into 8-24 kHz, quieter each time
        target = np.flatnonzero(source) + int(round(shift / (freqs[1] - freqs[0])))
        keep = target < len(X)
        X[target[keep]] += level * X[np.flatnonzero(source)[keep]]
        level *= 0.5
    out = np.fft.irfft(X, len(chunk))
    out = 0.5 * out / np.abs(out).max()                     # AudioSR's own rescaling
    pad = int(np.ceil(len(out) / (5.12 * SR)) * 5.12 * SR) - len(out)
    return np.pad(out, (0, pad)).astype(np.float32)


def band_db(x, low, high):
    X = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(X[(f >= low) & (f < high)].mean() + 1e-30)


mp3 = load_audio("bad.mp3")
long_input = np.tile(mp3.samples, (3, 1))                   # 12 s, so several chunks are needed
cutoff = measure_bandwidth(mp3).cutoff_hz

with patch.object(audiosr_adapter, "_load_model", return_value="fake-model"), \
     patch.object(audiosr_adapter, "_run_model", side_effect=fake_audiosr):
    out = audiosr_adapter.extend_with_audiosr(long_input, mp3.sample_rate, cutoff, device)
    silent_out = audiosr_adapter.extend_with_audiosr(np.zeros_like(long_input), mp3.sample_rate, cutoff, device)

base = resample(long_input, mp3.sample_rate, SR, device)
print(f"Shape            : {long_input.shape} at {mp3.sample_rate} Hz -> {out.shape} at {SR} Hz")
print(f"AI calls         : {calls['count']} (3 chunks x 2 channels)")

before = measure_bandwidth(AudioData(base, SR)).cutoff_hz
after = measure_bandwidth(AudioData(out, SR)).cutoff_hz
print(f"Cutoff           : {before:.0f} Hz -> {after:.0f} Hz")

low_cut = cutoff - audiosr_adapter.BLEND_WIDTH_HZ
lowpass = lambda x: np.fft.irfft(np.fft.rfft(x) * (np.fft.rfftfreq(len(x), 1 / SR) < low_cut), len(x))
err = lowpass(out[:, 0]) - lowpass(base[:, 0])
print(f"Original below {low_cut / 1000:.1f} kHz changed by: {10 * np.log10(np.mean(err ** 2) / np.mean(lowpass(base[:, 0]) ** 2)):.1f} dB (lower = untouched)")

print(f"Level 6-7 kHz    : {band_db(out[:, 0], 6_000, 7_000):6.1f} dB")
print(f"Level 9-10 kHz   : {band_db(base[:, 0], 9_000, 10_000):6.1f} dB before -> {band_db(out[:, 0], 9_000, 10_000):6.1f} dB after")
print(f"Silent input     : max output {np.abs(silent_out).max():.1f}, AI calls still {calls['count']}")
