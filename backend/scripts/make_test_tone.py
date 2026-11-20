import numpy as np
import soundfile as sf

sr = 44_100
t = np.arange(sr * 2) / sr                    # 2 seconds of time points
tone = 0.5 * np.sin(2 * np.pi * 440 * t)      # 440 Hz sine wave at half volume
stereo = np.column_stack([tone, tone])        # same tone in left and right
sf.write("test_tone.wav", stereo, sr)
print("Wrote test_tone.wav")
