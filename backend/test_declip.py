import numpy as np

from app.ai_pipeline import declip
from app.dsp_diagnostics import AudioData, detect_clipping, load_audio

audio = load_audio("declip_input.wav")
truth = np.load("declip_original.npy")

fixed, repaired = declip(audio.samples)


def error_db(signal: np.ndarray) -> float:
    err = signal[:, 0] - truth
    return 10 * np.log10(np.mean(err ** 2) / np.mean(truth ** 2))


print(f"Flat tops repaired      : {repaired}")
print(f"Error before (clipped)  : {error_db(audio.samples):6.1f} dB")
print(f"Error after (de-clipped): {error_db(fixed):6.1f} dB   (lower = closer to the original)")
print(f"Peak before / after     : {np.abs(audio.samples).max():.2f} / {np.abs(fixed).max():.2f}")

# Turn it down so it fits, then ask the detector from Step 5 again
fitted = fixed * (0.98 / np.abs(fixed).max())
before = detect_clipping(audio)
after = detect_clipping(AudioData(fitted, audio.sample_rate))
print(f"Detector before / after : is_clipped={before.is_clipped} / is_clipped={after.is_clipped}")
