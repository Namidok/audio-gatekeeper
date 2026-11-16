import logging

import numpy as np
import torch
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("PyTorch version :", torch.__version__)
    print("CUDA available  :", torch.cuda.is_available())
    print("MPS available   :", torch.backends.mps.is_available())
    device = pick_device()
    print("Selected device :", describe_device(device))
