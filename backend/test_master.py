import numpy as np
import soundfile as sf

from app.ai_pipeline import export_wav, master, measure_true_peak, pick_device, resample
from app.dsp_diagnostics import load_audio

# 1. Why "true peak": the highest sample is not the highest point of the wave
tone = load_audio("between_peaks.wav")
print(f"between_peaks.wav: highest sample {20 * np.log10(np.abs(tone.samples).max()):.1f} dBFS, "
      f"true peak {measure_true_peak(tone.samples):.1f} dBTP\n")

# 2. Master every test file and save it as a 24-bit WAV
device = pick_device()
for name in ["quiet.wav", "loud.wav", "drums.wav", "extreme.wav", "silence.wav"]:
    audio = load_audio(name)
    at_48k = resample(audio.samples, audio.sample_rate, 48_000, device)
    r = master(at_48k, 48_000)

    out_path = export_wav(r.samples, 48_000, f"mastered/{name}")
    info = sf.info(str(out_path))

    lufs_in = f"{r.input_lufs:6.1f}" if r.input_lufs is not None else "   n/a"
    lufs_out = f"{r.output_lufs:6.1f}" if r.output_lufs is not None else "   n/a"
    print(f"{name:12} LUFS {lufs_in} -> {lufs_out}   gain {r.gain_db:+5.1f} dB   "
          f"limiter {r.limiter_reduction_db:3.1f} dB   true peak {r.true_peak_dbtp:6.1f} dBTP   "
          f"{info.samplerate} Hz {info.subtype}")
