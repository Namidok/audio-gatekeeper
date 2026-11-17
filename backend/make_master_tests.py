import numpy as np
import soundfile as sf

sr = 48_000
t = np.arange(sr * 6) / sr


def drum_track(pad_level: float, hit_level: float) -> np.ndarray:
    """A soft pad with a drum hit every half second."""
    rng = np.random.default_rng(seed=21)
    pad = pad_level * np.sin(2 * np.pi * 220 * t) + 0.3 * pad_level * rng.standard_normal(len(t))
    hits = np.zeros_like(t)
    for start in range(0, len(t), sr // 2):
        n = min(7_200, len(t) - start)
        hits[start:start + n] = hit_level * np.exp(-np.arange(n) / 1_200) * rng.standard_normal(n)
    track = pad + hits
    return np.column_stack([track, track])


sf.write("drums.wav", drum_track(0.15, 0.4), sr, subtype="FLOAT")     # normal dynamics
sf.write("extreme.wav", drum_track(0.06, 0.7), sr, subtype="FLOAT")   # huge peaks, quiet body

# Sample peak lies: a 12 kHz tone sampled exactly between its crests
tone = 0.9 * np.sin(2 * np.pi * 12_000 * t + np.pi / 4)
sf.write("between_peaks.wav", np.column_stack([tone, tone]), sr, subtype="FLOAT")
print("Wrote drums.wav, extreme.wav, between_peaks.wav")
