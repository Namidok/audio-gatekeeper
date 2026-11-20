import numpy as np
import soundfile as sf

sr = 44_100
rng = np.random.default_rng(seed=3)
noise = rng.standard_normal(sr * 3)
noise = noise / np.abs(noise).max()

quiet = 0.05 * noise      # a very quiet recording
loud = 0.9 * noise        # a loud recording

sf.write("quiet.wav", np.column_stack([quiet, quiet]), sr)
sf.write("loud.wav", np.column_stack([loud, loud]), sr)
sf.write("silence.wav", np.zeros((sr * 3, 2)), sr)
print("Wrote quiet.wav, loud.wav, silence.wav")
