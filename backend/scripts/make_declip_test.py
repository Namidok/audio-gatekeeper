import numpy as np
import soundfile as sf

sr = 44_100
t = np.arange(sr * 3) / sr
rng = np.random.default_rng(seed=5)

# A chord (A, C#, E, A) with 5 harmonics per note, slowly swelling in volume
music = np.zeros_like(t)
for note in (220.0, 277.18, 329.63, 440.0):
    for harmonic in range(1, 6):
        phase = rng.uniform(0, 2 * np.pi)
        music += (0.6 ** harmonic) * np.sin(2 * np.pi * note * harmonic * t + phase)
music *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t)
music /= np.abs(music).max()

original = 1.5 * music                          # peaks reach 1.5 (too loud)
clipped = np.clip(original, -1.0, 1.0)          # what a real recorder would save

np.save("declip_original.npy", original)        # the "truth", for comparison
sf.write("declip_input.wav", np.column_stack([clipped, clipped]), sr, subtype="FLOAT")
print("Wrote declip_input.wav")
