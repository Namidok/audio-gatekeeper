import logging
from dataclasses import dataclass

import numpy as np
import torch
import torchaudio.functional as AF
from scipy.interpolate import CubicSpline

from app.config import get_settings

log = logging.getLogger(__name__)

VALID_CHOICES = ("auto", "cuda", "mps", "cpu")


# --------------------------------------------------------------------------- #
# Step 10: choosing the compute device
# --------------------------------------------------------------------------- #
def _works(device: torch.device) -> bool:
    """Run a tiny calculation to prove the device really works, not just exists."""
    try:
        x = torch.ones(8, device=device)
        return float((x * 2).sum().item()) == 16.0
    except Exception as exc:
        log.warning("Device %s is present but failed a test calculation: %s", device, exc)
        return False


def _is_available(name: str) -> bool:
    if name == "cuda":
        return torch.cuda.is_available()
    if name == "mps":
        return torch.backends.mps.is_available()
    return name == "cpu"


def pick_device(choice: str | None = None) -> torch.device:
    choice = (choice or get_settings().compute_device).lower()
    if choice not in VALID_CHOICES:
        raise ValueError(f"compute_device must be one of {VALID_CHOICES}, got '{choice}'")

    if choice == "auto":
        candidates = ["cuda", "mps", "cpu"]
    else:
        candidates = [choice, "cpu"]          # what was asked for, then the safe fallback

    for name in candidates:
        if _is_available(name):
            device = torch.device(name)
            if _works(device):
                if choice not in ("auto", name):
                    log.warning("'%s' was requested but is not usable; falling back to %s", choice, name)
                return device
    return torch.device("cpu")


def describe_device(device: torch.device) -> str:
    if device.type == "cuda":
        return f"cuda ({torch.cuda.get_device_name(device)})"
    if device.type == "mps":
        return "mps (Apple Silicon GPU)"
    return "cpu"


# --------------------------------------------------------------------------- #
# Step 11: de-clipping
# --------------------------------------------------------------------------- #
def _clipped_runs(channel: np.ndarray, threshold: float, min_run: int,
                  tolerance: float) -> list[tuple[int, int]]:
    """Return (start, end) of every flat-topped region, same rule as the detector."""
    loud = np.abs(channel) >= threshold
    step = np.abs(np.diff(channel)) <= tolerance
    same_as_prev = np.concatenate(([False], step))
    same_as_next = np.concatenate((step, [False]))
    flat = loud & (same_as_prev | same_as_next)

    padded = np.concatenate(([0], flat.astype(np.int8), [0]))
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= min_run]


def declip_channel(channel: np.ndarray, threshold: float = 0.99, min_run: int = 3,
                   tolerance: float = 1e-5, anchors: int = 4,
                   max_boost: float = 2.0) -> tuple[np.ndarray, int]:
    out = channel.astype(np.float64).copy()
    runs = _clipped_runs(channel, threshold, min_run, tolerance)
    repaired = 0

    for start, end in runs:
        left = np.arange(max(0, start - anchors), start)
        right = np.arange(end, min(len(channel), end + anchors))
        if len(left) < 2 or len(right) < 2:
            continue                                  # too close to the file edge

        x = np.concatenate((left, right))
        spline = CubicSpline(x, channel[x].astype(np.float64))
        gap = np.arange(start, end)
        guess = spline(gap)

        clip_level = channel[start:end].astype(np.float64)
        sign = np.sign(clip_level)
        # The real peak was at least as tall as the flat top, and not absurdly taller
        magnitude = np.clip(np.abs(guess), np.abs(clip_level), np.abs(clip_level) * max_boost)
        out[gap] = sign * magnitude
        repaired += 1

    return out.astype(np.float32), repaired


def declip(samples: np.ndarray, threshold: float = 0.99) -> tuple[np.ndarray, int]:
    """De-clip every channel of a (samples, channels) array. Peaks may now exceed 1.0."""
    repaired_total = 0
    channels = []
    for ch in range(samples.shape[1]):
        fixed, repaired = declip_channel(samples[:, ch], threshold=threshold)
        channels.append(fixed)
        repaired_total += repaired
    return np.stack(channels, axis=1), repaired_total


# --------------------------------------------------------------------------- #
# Step 12: resampling to 48 kHz (on the GPU when there is one)
# --------------------------------------------------------------------------- #
def resample(samples: np.ndarray, orig_sr: int, target_sr: int,
             device: torch.device) -> np.ndarray:
    """Change the sample rate of a (samples, channels) array. Peaks above 1.0 are kept."""
    if orig_sr == target_sr:
        return samples.astype(np.float32, copy=False)

    # PyTorch audio wants (channels, samples), so flip the array before sending it
    waveform = torch.from_numpy(np.ascontiguousarray(samples.T, dtype=np.float32)).to(device)
    with torch.inference_mode():
        out = AF.resample(
            waveform,
            orig_freq=orig_sr,
            new_freq=target_sr,
            lowpass_filter_width=64,              # longer filter = cleaner result
            rolloff=0.9475937167399596,           # keep everything up to ~95% of Nyquist
            resampling_method="sinc_interp_kaiser",
            beta=14.769656459379492,              # strong suppression of aliasing
        )
    return out.T.contiguous().cpu().numpy()


# --------------------------------------------------------------------------- #
# Step 13: bandwidth extension (AI super-resolution) with a safe fallback
# --------------------------------------------------------------------------- #
@dataclass
class UpsampleResult:
    samples: np.ndarray     # (samples, channels) at target_sr
    sample_rate: int
    method: str             # "audiosr" or "resample_only"
    note: str               # plain-language explanation for the report card


def audiosr_available() -> bool:
    """True only if AudioSR AND our adapter for it can both be imported."""
    try:
        import audiosr  # noqa: F401
        from app import audiosr_adapter  # noqa: F401
        return True
    except Exception:               # ImportError, or version clashes inside AudioSR
        return False


def bandwidth_extend(samples: np.ndarray, orig_sr: int, cutoff_hz: float,
                     device: torch.device, target_sr: int = 48_000) -> UpsampleResult:
    settings = get_settings()

    if not settings.enable_audiosr:
        note = "AudioSR is switched off (ENABLE_AUDIOSR=false); highs not restored"
    elif not audiosr_available():
        note = "AudioSR is not installed on this worker; highs not restored"
    else:
        try:
            from app.audiosr_adapter import extend_with_audiosr
            out = extend_with_audiosr(samples, orig_sr, cutoff_hz, device, target_sr)
            return UpsampleResult(out, target_sr, "audiosr",
                                  f"High frequencies above {cutoff_hz / 1000:.1f} kHz rebuilt with AudioSR")
        except Exception as exc:
            log.exception("AudioSR failed; falling back to plain resampling")
            note = f"AudioSR failed ({exc.__class__.__name__}); highs not restored"

    log.warning(note)
    out = resample(samples, orig_sr, target_sr, device)
    return UpsampleResult(out, target_sr, "resample_only", note)
