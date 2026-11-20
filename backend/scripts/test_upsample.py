import logging
import types
from unittest.mock import patch

from app.config import get_settings
from app.ai_pipeline import audiosr_available, bandwidth_extend, pick_device
from app.dsp_diagnostics import load_audio, measure_bandwidth

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
device = pick_device()
audio = load_audio("bad.mp3")
cutoff = measure_bandwidth(audio).cutoff_hz

print("AudioSR available:", audiosr_available())

# Case 1: the normal path on this machine
r = bandwidth_extend(audio.samples, audio.sample_rate, cutoff, device)
print(f"Case 1 -> method={r.method}  rate={r.sample_rate}  shape={r.samples.shape}")
print(f"          note: {r.note}")


# Case 2: pretend AudioSR is installed but the GPU runs out of memory mid-job
def crashing_audiosr(*args, **kwargs):
    raise RuntimeError("CUDA out of memory (simulated)")


fake_adapter = types.ModuleType("app.audiosr_adapter")
fake_adapter.extend_with_audiosr = crashing_audiosr

with patch("app.ai_pipeline.audiosr_import_error", return_value=None), \
     patch.object(get_settings(), "allow_audiosr_without_cuda", True), \
     patch.dict("sys.modules", {"app.audiosr_adapter": fake_adapter}):
    r = bandwidth_extend(audio.samples, audio.sample_rate, cutoff, device)
print(f"Case 2 -> method={r.method}  rate={r.sample_rate}  shape={r.samples.shape}")
print(f"          note: {r.note}")
