import numpy as np
import soundfile as sf

sr = 44_100
t = np.arange(sr * 2) / sr
sine = np.sin(2 * np.pi * 440 * t)

loud_clean = 0.995 * sine                     # very loud, but NOT clipped
clipped = np.clip(2.0 * sine, -1.0, 1.0)      # pushed too hard, tops cut off

sf.write("loud_clean.wav", np.column_stack([loud_clean, loud_clean]), sr)
sf.write("clipped.wav", np.column_stack([clipped, clipped]), sr)
print("Wrote loud_clean.wav and clipped.wav")
