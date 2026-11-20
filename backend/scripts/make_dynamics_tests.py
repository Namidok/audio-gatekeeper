import numpy as np
import soundfile as sf

sr = 44_100
rng = np.random.default_rng(seed=42)
noise = rng.standard_normal(sr * 2)

dynamic = 0.15 * noise                      # natural peaks and valleys
squashed = 0.9 * np.tanh(4.0 * noise)       # tanh flattens everything loud

sf.write("dynamic.wav", np.column_stack([dynamic, dynamic]), sr)
sf.write("squashed.wav", np.column_stack([squashed, squashed]), sr)
print("Wrote dynamic.wav and squashed.wav")
