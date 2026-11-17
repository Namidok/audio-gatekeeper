import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
import torch
import torchaudio.functional as AF
from scipy.interpolate import CubicSpline
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import resample_poly

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


# --------------------------------------------------------------------------- #
# Step 15: mastering (-14 LUFS, -1 dBTP, 24-bit WAV)
# --------------------------------------------------------------------------- #
@dataclass
class MasterResult:
    samples: np.ndarray            # mastered audio, (samples, channels)
    input_lufs: float | None
    output_lufs: float | None
    gain_db: float                 # volume change applied before limiting
    limiter_reduction_db: float    # deepest dip the limiter made (0 = never touched)
    true_peak_dbtp: float


def _db(value: float) -> float:
    return float(20.0 * np.log10(max(value, 1e-10)))


def _true_peak_envelope(samples: np.ndarray, factor: int = 4,
                        block: int = 480_000, pad: int = 64) -> np.ndarray:
    """Loudest point near every sample, including peaks *between* samples."""
    n = samples.shape[0]
    envelope = np.abs(samples).max(axis=1).astype(np.float32)
    for start in range(0, n, block):                     # in 10 s blocks to save memory
        end = min(start + block, n)
        lo, hi = max(0, start - pad), min(n, end + pad)
        up = resample_poly(samples[lo:hi].astype(np.float32), factor, 1, axis=0)
        up = np.abs(up).max(axis=1)[(start - lo) * factor:(end - lo) * factor]
        envelope[start:end] = np.maximum(envelope[start:end], up.reshape(-1, factor).max(axis=1))
    return envelope


def measure_true_peak(samples: np.ndarray) -> float:
    return _db(float(_true_peak_envelope(samples).max()))


def limit_true_peak(samples: np.ndarray, sample_rate: int, ceiling_dbtp: float,
                    window_ms: float = 5.0) -> tuple[np.ndarray, float]:
    """Smoothly turn down only the moments that would poke above the ceiling."""
    ceiling = 10 ** (ceiling_dbtp / 20)
    peaks = _true_peak_envelope(samples)
    needed = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-10))   # gain each sample needs

    width = 2 * int(sample_rate * window_ms / 1000) + 1
    gain = minimum_filter1d(needed, size=width, mode="nearest")    # start turning down early...
    gain = uniform_filter1d(gain, size=width, mode="nearest")      # ...and ramp smoothly
    return (samples * gain[:, None]).astype(np.float32), max(0.0, -_db(float(gain.min())))


def master(samples: np.ndarray, sample_rate: int, target_lufs: float = -14.0,
           ceiling_dbtp: float = -1.0, max_limit_db: float = 6.0,
           max_rounds: int = 4) -> MasterResult:
    meter = pyln.Meter(sample_rate)
    source = samples.astype(np.float32)

    def loudness(x: np.ndarray) -> float | None:
        if x.shape[0] / sample_rate < meter.block_size:
            return None
        value = meter.integrated_loudness(x.astype(np.float64))
        return float(value) if np.isfinite(value) else None

    def apply(gain_db: float) -> tuple[np.ndarray, float]:
        louder = source * np.float32(10 ** (gain_db / 20))                 # 1. change volume
        return limit_true_peak(louder, sample_rate, ceiling_dbtp - 0.1)    # 2. catch peaks

    input_lufs = loudness(source)
    if input_lufs is None:                                                 # silent or too short
        return MasterResult(source, None, None, 0.0, 0.0, measure_true_peak(source))

    gain_db = target_lufs - input_lufs
    for _ in range(max_rounds):
        out, cut_db = apply(gain_db)
        if cut_db > max_limit_db:
            # Reaching -14 LUFS would mean crushing the peaks: back off and accept quieter
            gain_db -= cut_db - max_limit_db
            out, cut_db = apply(gain_db)
            break
        current = loudness(out)                                            # 3. limiting costs a
        if current is None or abs(current - target_lufs) <= 0.1:           #    little loudness,
            break                                                          #    so nudge and retry
        gain_db += target_lufs - current

    return MasterResult(
        samples=out,
        input_lufs=input_lufs,
        output_lufs=loudness(out),
        gain_db=gain_db,
        limiter_reduction_db=cut_db,
        true_peak_dbtp=measure_true_peak(out),
    )


def export_wav(samples: np.ndarray, sample_rate: int, path: str | Path) -> Path:
    """Save as 24-bit PCM WAV, the format the spec asks for."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(samples, -1.0, 1.0), sample_rate, subtype="PCM_24")
    return path


# --------------------------------------------------------------------------- #
# Step 16: enhance() runs the whole chain in order
# --------------------------------------------------------------------------- #
@dataclass
class EnhanceResult:
    output_path: Path
    device: str
    ai_bypassed: bool                    # True = pristine track, no AI was needed
    steps_applied: list[str] = field(default_factory=list)
    steps_skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    output_sample_rate: int = 48_000
    output_bit_depth: int = 24
    input_lufs: float | None = None
    output_lufs: float | None = None
    output_true_peak_dbtp: float | None = None
    limiter_reduction_db: float = 0.0
    processing_seconds: float = 0.0


def enhance(input_path: str | Path, output_path: str | Path, report=None,
            on_stage: Callable[[str], None] | None = None) -> EnhanceResult:
    """Diagnose (if needed) -> de-clip -> upsample -> master -> export 24-bit WAV."""
    from app.dsp_diagnostics import analyze, load_audio   # imported here to keep the two modules independent

    settings = get_settings()
    started = time.perf_counter()
    stage = on_stage or (lambda name: None)           # the worker will use this to report progress

    if report is None:
        stage("diagnosing")
        report = analyze(input_path)

    device = pick_device()
    target_sr = settings.target_sample_rate
    result = EnhanceResult(Path(output_path), describe_device(device),
                           ai_bypassed=not (report.flags.declip or report.flags.upsample),
                           output_sample_rate=target_sr)
    try:
        audio = load_audio(input_path)
        samples, sr = audio.samples, audio.sample_rate

        # 1. De-clip, only if the gatekeeper found clipping
        if report.flags.declip:
            stage("enhancing")
            samples, repaired = declip(samples, threshold=settings.clip_threshold)
            result.steps_applied.append(f"declip ({repaired} flat tops repaired)")
        else:
            result.steps_skipped.append("declip (no clipping found)")

        # 2. Upsample: AI bandwidth extension if flagged, otherwise plain resampling
        if report.flags.upsample:
            stage("enhancing")
            up = bandwidth_extend(samples, sr, report.bandwidth.cutoff_hz, device, target_sr)
            samples, sr = up.samples, up.sample_rate
            result.steps_applied.append(f"bandwidth extension ({up.method})")
            result.notes.append(up.note)
        else:
            result.steps_skipped.append("bandwidth extension (full bandwidth already)")
            if sr != target_sr:
                samples, sr = resample(samples, sr, target_sr, device), target_sr
                result.steps_applied.append(f"resample {audio.sample_rate} Hz -> {target_sr} Hz")

        # 3. Master: every track, AI or not
        stage("mastering")
        mastered = master(samples, sr, settings.target_lufs, settings.true_peak_ceiling_dbtp)
        result.steps_applied.append(
            f"master to {settings.target_lufs:.0f} LUFS / {settings.true_peak_ceiling_dbtp:.0f} dBTP")
        if mastered.output_lufs is None:
            result.notes.append("Silent or too short to measure: loudness left unchanged")
        elif mastered.output_lufs < settings.target_lufs - 0.5:
            result.notes.append(f"Kept at {mastered.output_lufs:.1f} LUFS to protect the peaks "
                                f"(reaching {settings.target_lufs:.0f} LUFS would need heavy limiting)")

        # 4. Export
        export_wav(mastered.samples, sr, result.output_path)

        result.input_lufs = mastered.input_lufs
        result.output_lufs = mastered.output_lufs
        result.output_true_peak_dbtp = mastered.true_peak_dbtp
        result.limiter_reduction_db = mastered.limiter_reduction_db
    finally:
        if device.type == "cuda":
            torch.cuda.empty_cache()                  # hand GPU memory back for the next job

    result.processing_seconds = round(time.perf_counter() - started, 2)
    return result
