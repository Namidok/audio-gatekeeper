import time

import numpy as np

from app.ai_pipeline import describe_device, pick_device, resample
from app.dsp_diagnostics import AudioData, load_audio, measure_bandwidth

device = pick_device()
print("Device:", describe_device(device))

# 1. A 1 kHz tone at 44.1 kHz: does it stay 1 kHz, same volume, right length?
sr = 44_100
t = np.arange(sr * 3) / sr
tone = 0.5 * np.sin(2 * np.pi * 1000 * t)
stereo = np.column_stack([tone, tone]).astype(np.float32)

started = time.perf_counter()
out = resample(stereo, sr, 48_000, device)
elapsed = time.perf_counter() - started

spectrum = np.abs(np.fft.rfft(out[:, 0]))
peak_hz = np.fft.rfftfreq(len(out), 1 / 48_000)[spectrum.argmax()]
print(f"Length    : {len(stereo)} -> {len(out)} samples (3 s at 48 kHz = 144000)")
print(f"Pitch     : {peak_hz:.0f} Hz (should stay 1000 Hz)")
print(f"Peak level: {np.abs(stereo).max():.3f} -> {np.abs(out).max():.3f}")
print(f"Time      : {elapsed:.3f} s")

# 2. De-clipped audio has peaks above 1.0: they must survive resampling
loud = (stereo * 3.0)
print(f"Peaks above 1.0 kept: {np.abs(resample(loud, sr, 48_000, device)).max():.2f} (input {np.abs(loud).max():.2f})")

# 3. Resampling alone does NOT bring back the highs an MP3 threw away
mp3 = load_audio("bad.mp3")
up = resample(mp3.samples, mp3.sample_rate, 48_000, device)
before = measure_bandwidth(mp3).cutoff_hz
after = measure_bandwidth(AudioData(up, 48_000)).cutoff_hz
print(f"bad.mp3 cutoff: {before:.0f} Hz -> {after:.0f} Hz after resampling (still band-limited)")
