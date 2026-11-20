"""Make three test versions of a real song (30 seconds from the middle)."""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

source = Path(sys.argv[1]).expanduser()
audio, sr = sf.read(str(source), dtype="float32", always_2d=True)

length = min(len(audio), 30 * sr)
start = max(0, (len(audio) - length) // 2)
clip = audio[start:start + length]
print(f"{source.name}: {sr} Hz, using {length / sr:.0f} s from {start / sr:.0f} s")

sf.write("test_clean.wav", clip, sr, subtype="PCM_16")

too_loud = clip * 10 ** (9 / 20)
sf.write("test_clipped.wav", np.clip(too_loud, -1.0, 1.0), sr, subtype="PCM_16")
print(f"  clipped samples: {np.mean(np.abs(too_loud) >= 1.0) * 100:.1f}%")

sf.write("test_lowbitrate.mp3", clip, sr, format="MP3", compression_level=0.9)
print("Wrote test_clean.wav, test_clipped.wav, test_lowbitrate.mp3")
