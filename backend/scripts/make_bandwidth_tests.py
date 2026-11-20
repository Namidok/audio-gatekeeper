import numpy as np
import soundfile as sf

sr = 44_100
rng = np.random.default_rng(seed=7)
n = sr * 4

# "Music-like" noise: strong bass, weaker treble, but content all the way to 22 kHz
spectrum = np.fft.rfft(rng.standard_normal(n))
freqs = np.fft.rfftfreq(n, 1 / sr)
freqs[0] = 1.0
music = np.fft.irfft(spectrum / np.sqrt(freqs), n)
music = 0.8 * music / np.abs(music).max()
stereo = np.column_stack([music, music])

sf.write("full_band.wav", stereo, sr)                                  # original
sf.write("good.mp3", stereo, sr, format="MP3", compression_level=0.5)  # high bitrate
sf.write("bad.mp3", stereo, sr, format="MP3", compression_level=0.9)   # low bitrate
print("Wrote full_band.wav, good.mp3, bad.mp3")
