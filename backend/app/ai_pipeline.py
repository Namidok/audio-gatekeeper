"""The rescue engine: device choice, de-clipping, resampling / AI bandwidth extension, mastering.

enhance() runs: diagnose (if needed) -> de-clip (if flagged) -> upsample (AI if flagged) ->
master (every track) -> 24-bit WAV export. Every step records what it really did, so the UI
never claims a repair that didn't happen.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
import torch
import torchaudio.functional as AF
from scipy.interpolate import CubicSpline
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import firwin, resample_poly

from app.config import LOUD_MASTER_LUFS, get_settings
from app.dsp_diagnostics import (
    AudioData,
    DiagnosticReport,
    analyze,
    clip_level,
    detect_clipping,
    flat_top_runs,
    load_audio,
    measure_bandwidth,
    to_db,
)

log = logging.getLogger(__name__)

VALID_CHOICES = ("auto", "cuda", "mps", "cpu")
AUDIOSR_RATE = 48_000   # the only rate AudioSR produces; other targets are resampled afterwards

Outcome = Literal["bypassed", "repaired", "partial", "no_signal"]
UpsampleMethod = Literal["audiosr", "resample_only", "none"]
StepKind = Literal["declip", "bandwidth_extension", "resample", "master"]
StepStatus = Literal["applied", "skipped", "fallback"]


# --------------------------------------------------------------------------- #
# Choosing the compute device
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

    candidates = ["cuda", "mps", "cpu"] if choice == "auto" else [choice, "cpu"]
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
# De-clipping
# --------------------------------------------------------------------------- #
def declip_channel(channel: np.ndarray, level: float, min_run: int = 3,
                   tolerance: float = 1e-5, anchors: int = 4,
                   max_boost: float = 2.0) -> tuple[np.ndarray, int]:
    """Rebuild flat tops with a cubic spline through the samples around them."""
    out = channel.astype(np.float64).copy()
    repaired = 0

    for start, end in flat_top_runs(channel, level, min_run, tolerance):   # same rule as the detector
        left = np.arange(max(0, start - anchors), start)
        right = np.arange(end, min(len(channel), end + anchors))
        if len(left) < 2 or len(right) < 2:
            continue                                  # too close to the file edge

        x = np.concatenate((left, right))
        spline = CubicSpline(x, channel[x].astype(np.float64))
        gap = np.arange(start, end)
        guess = spline(gap)

        flat = channel[start:end].astype(np.float64)
        sign = np.sign(flat)
        # The real peak was at least as tall as the flat top, and not absurdly taller
        magnitude = np.clip(np.abs(guess), np.abs(flat), np.abs(flat) * max_boost)
        out[gap] = sign * magnitude
        repaired += 1

    return out.astype(np.float32), repaired


def declip(samples: np.ndarray, threshold: float = 0.99) -> tuple[np.ndarray, int]:
    """De-clip every channel of a (samples, channels) array. Peaks may now exceed 1.0."""
    level = clip_level(samples, threshold)
    if level is None:
        return samples.astype(np.float32, copy=False), 0
    repaired_total = 0
    channels = []
    for ch in range(samples.shape[1]):
        fixed, repaired = declip_channel(samples[:, ch], level, tolerance=1e-5 * level)  # same rule as detector
        channels.append(fixed)
        repaired_total += repaired
    return np.stack(channels, axis=1), repaired_total


# --------------------------------------------------------------------------- #
# Resampling to the target rate (on the GPU when there is one)
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
# Bandwidth extension (AI super-resolution) with an honest fallback
# --------------------------------------------------------------------------- #
@dataclass
class UpsampleResult:
    samples: np.ndarray     # (samples, channels) at target_sr
    sample_rate: int
    method: UpsampleMethod
    note: str               # plain-language explanation for the report card


@lru_cache(maxsize=1)
def audiosr_import_error() -> str | None:
    """None if AudioSR AND our adapter can be imported, otherwise the reason (logged once)."""
    try:
        import audiosr  # noqa: F401
        from app import audiosr_adapter  # noqa: F401
        return None
    except ImportError:
        return "not installed"
    except Exception as exc:               # installed, but broken (e.g. a version clash inside AudioSR)
        log.exception("AudioSR is installed but failed to import")
        return f"installed but failed to load ({exc.__class__.__name__})"


def audiosr_available() -> bool:
    return audiosr_import_error() is None


def bandwidth_extend(samples: np.ndarray, orig_sr: int, cutoff_hz: float,
                     device: torch.device, target_sr: int = 48_000) -> UpsampleResult:
    settings = get_settings()

    if not settings.enable_audiosr:
        note = "High frequencies were not restored: AI restoration is switched off on this server."
    elif (problem := audiosr_import_error()) is not None:
        note = f"High frequencies were not restored: the AI model is {problem} on this server."
    elif device.type != "cuda" and not settings.allow_audiosr_without_cuda:
        note = "High frequencies were not restored: the AI model needs a CUDA GPU, and this server has none."
    else:
        try:
            from app.audiosr_adapter import extend_with_audiosr
            out = extend_with_audiosr(samples, orig_sr, cutoff_hz, device, AUDIOSR_RATE)  # AudioSR is 48 kHz only
            if target_sr != AUDIOSR_RATE:
                out = resample(out, AUDIOSR_RATE, target_sr, device)
            return UpsampleResult(out, target_sr, "audiosr",
                                  f"High frequencies above {cutoff_hz / 1000:.1f} kHz were rebuilt by AI (AudioSR).")
        except Exception as exc:
            if type(exc).__name__ == "SoftTimeLimitExceeded":   # Celery's time limit: never swallow it
                raise
            log.exception("AudioSR failed; falling back to plain resampling")
            note = f"High frequencies were not restored: the AI model failed ({exc.__class__.__name__})."

    log.warning(note)
    return UpsampleResult(resample(samples, orig_sr, target_sr, device), target_sr, "resample_only", note)


# --------------------------------------------------------------------------- #
# True peak (oversampled, ITU-R BS.1770 Annex 2 style) and limiting
# --------------------------------------------------------------------------- #
LIMITER_FACTOR = 8         # oversampling used while limiting (worst-case under-read ~0.12 dB at 20 kHz)
REPORT_FACTOR = 16         # oversampling used for the reported value (worst case ~0.03 dB)
LIMITER_MARGIN_DB = 0.2    # aim this far below the ceiling to absorb the limiter meter's error


@lru_cache(maxsize=4)
def _oversampling_filter(factor: int, taps_per_phase: int = 32, beta: float = 10.0) -> np.ndarray:
    return firwin(factor * taps_per_phase + 1, 1 / factor, window=("kaiser", beta)).astype(np.float32)


def _true_peak_envelope(samples: np.ndarray, factor: int = LIMITER_FACTOR,
                        block: int = 480_000, pad: int = 64) -> np.ndarray:
    """Loudest point near every sample, including peaks *between* samples."""
    h = _oversampling_filter(factor)
    n = samples.shape[0]
    envelope = np.abs(samples).max(axis=1).astype(np.float32)
    for start in range(0, n, block):                     # in 10 s blocks to save memory
        end = min(start + block, n)
        lo, hi = max(0, start - pad), min(n, end + pad)
        up = resample_poly(samples[lo:hi].astype(np.float32), factor, 1, axis=0, window=h)
        up = np.abs(up).max(axis=1)[(start - lo) * factor:(end - lo) * factor]
        envelope[start:end] = np.maximum(envelope[start:end], up.reshape(-1, factor).max(axis=1))
    return envelope


def measure_true_peak(samples: np.ndarray, factor: int = REPORT_FACTOR) -> float:
    """True peak in dBTP (max over channels), measured with `factor`x oversampling."""
    return to_db(float(_true_peak_envelope(samples, factor).max()))


def limit_true_peak(samples: np.ndarray, sample_rate: int, ceiling_dbtp: float,
                    window_ms: float = 5.0) -> tuple[np.ndarray, float]:
    """Smoothly turn down only the moments that would poke above the ceiling."""
    ceiling = 10 ** (ceiling_dbtp / 20)
    peaks = _true_peak_envelope(samples)
    needed = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-10))   # gain each sample needs

    width = 2 * int(sample_rate * window_ms / 1000) + 1
    gain = minimum_filter1d(needed, size=width, mode="nearest")    # start turning down early...
    gain = uniform_filter1d(gain, size=width, mode="nearest")      # ...and ramp smoothly
    return (samples * gain[:, None]).astype(np.float32), max(0.0, -to_db(float(gain.min())))


# --------------------------------------------------------------------------- #
# Mastering: loudness target (per platform profile) + true-peak ceiling, 24-bit WAV
# --------------------------------------------------------------------------- #
@dataclass
class MasterResult:
    samples: np.ndarray            # mastered audio, (samples, channels)
    input_lufs: float | None
    output_lufs: float | None
    gain_db: float                 # volume change applied before limiting
    limiter_reduction_db: float    # deepest dip the limiter made (0 = never touched)
    true_peak_dbtp: float          # measured with 16x oversampling
    ceiling_dbtp: float            # the ceiling that applied to this master


def master(samples: np.ndarray, sample_rate: int, target_lufs: float | None = -14.0,
           ceiling_dbtp: float = -1.0, loud_ceiling_dbtp: float | None = None,
           max_limit_db: float = 6.0, max_rounds: int = 4) -> MasterResult:
    """Reach target_lufs (None = keep the loudness) without true peaks above the ceiling.

    Guarantee: the returned true peak (16x oversampled) is <= the ceiling that applied.
    """
    meter = pyln.Meter(sample_rate)
    source = samples.astype(np.float32)

    def loudness(x: np.ndarray) -> float | None:
        if x.shape[0] / sample_rate < meter.block_size:
            return None
        value = meter.integrated_loudness(x.astype(np.float64))
        return float(value) if np.isfinite(value) else None

    input_lufs = loudness(source)
    if input_lufs is None:                                                 # silent or too short
        return MasterResult(source, None, None, 0.0, 0.0, measure_true_peak(source), ceiling_dbtp)

    # Spotify: a master louder than -14 LUFS should peak lower (more codec headroom)
    expected_out = input_lufs if target_lufs is None else target_lufs
    if loud_ceiling_dbtp is not None and expected_out > LOUD_MASTER_LUFS + 0.05:
        ceiling_dbtp = loud_ceiling_dbtp

    def apply(gain_db: float) -> tuple[np.ndarray, float]:
        louder = source * np.float32(10 ** (gain_db / 20))                           # 1. change volume
        return limit_true_peak(louder, sample_rate, ceiling_dbtp - LIMITER_MARGIN_DB)  # 2. catch peaks

    gain_db = 0.0 if target_lufs is None else target_lufs - input_lufs
    out, cut_db = apply(gain_db)
    for _ in range(max_rounds):
        if cut_db > max_limit_db:
            # Reaching the target would mean crushing the peaks: back off and accept quieter
            gain_db -= cut_db - max_limit_db
            out, cut_db = apply(gain_db)
            break
        if target_lufs is None:
            break
        current = loudness(out)                                            # 3. limiting costs a
        if current is None or abs(current - target_lufs) <= 0.1:           #    little loudness,
            break                                                          #    so nudge and retry
        gain_db += target_lufs - current
        out, cut_db = apply(gain_db)

    # 4. Safety net: verify with a finer meter and trim if anything still pokes above the ceiling
    true_peak = measure_true_peak(out)
    if true_peak > ceiling_dbtp:
        trim_db = ceiling_dbtp - true_peak - 0.02
        out = (out * np.float32(10 ** (trim_db / 20))).astype(np.float32)
        gain_db += trim_db
        true_peak = measure_true_peak(out)

    return MasterResult(
        samples=out,
        input_lufs=input_lufs,
        output_lufs=loudness(out),
        gain_db=gain_db,
        limiter_reduction_db=cut_db,
        true_peak_dbtp=true_peak,
        ceiling_dbtp=ceiling_dbtp,
    )


def export_wav(samples: np.ndarray, sample_rate: int, path: str | Path) -> Path:
    """Save as 24-bit PCM WAV, the delivery format."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(samples, -1.0, 1.0), sample_rate, subtype="PCM_24")
    return path


def export_preview(samples: np.ndarray, sample_rate: int, path: str | Path) -> Path:
    """Lossless 24-bit FLAC for in-browser playback (browser support for AIFF / 24-bit WAV varies)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(samples, -1.0, 1.0), sample_rate, format="FLAC", subtype="PCM_24")
    return path


# --------------------------------------------------------------------------- #
# enhance() runs the whole chain in order
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    kind: StepKind
    status: StepStatus
    summary: str          # plain language, for everyone
    detail: str = ""      # technical, for the "expert details" view


@dataclass
class OutputMetrics:
    clipped_ratio: float
    cutoff_hz: float
    nyquist_hz: float


@dataclass
class EnhanceResult:
    output_path: Path
    device: str
    ai_bypassed: bool                    # True = no repair was needed, the AI stage was skipped
    outcome: Outcome = "bypassed"
    upsample_method: UpsampleMethod = "none"
    highs_restored: bool = False         # True only if AudioSR really rebuilt the high frequencies
    steps: list[Step] = field(default_factory=list)
    steps_applied: list[str] = field(default_factory=list)
    steps_skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    loudness_profile: str = "spotify"
    loudness_profile_label: str = ""
    target_lufs: float | None = None
    ceiling_dbtp: float = -1.0
    output_sample_rate: int = 48_000
    output_bit_depth: int = 24
    input_lufs: float | None = None      # the original upload, untouched
    output_lufs: float | None = None
    input_true_peak_dbtp: float | None = None
    output_true_peak_dbtp: float | None = None
    limiter_reduction_db: float = 0.0
    output_metrics: OutputMetrics | None = None
    processing_seconds: float = 0.0
    # Not part of the public result: the mastered audio, kept in memory for previews/waveforms
    mastered: np.ndarray | None = field(default=None, repr=False, compare=False)

    def add_step(self, kind: StepKind, status: StepStatus, summary: str, detail: str = "") -> None:
        self.steps.append(Step(kind, status, summary, detail))
        (self.steps_skipped if status == "skipped" else self.steps_applied).append(summary)

    def to_public_dict(self) -> dict:
        """JSON-safe dict for the API: no server paths, no audio arrays."""
        data = {k: v for k, v in self.__dict__.items() if k not in ("output_path", "mastered")}
        data["steps"] = [asdict(s) for s in self.steps]
        data["output_metrics"] = asdict(self.output_metrics) if self.output_metrics else None
        return data


def enhance(input_path: str | Path, output_path: str | Path, report: DiagnosticReport | None = None,
            on_stage: Callable[[str], None] | None = None, *, audio: AudioData | None = None,
            profile_name: str | None = None) -> EnhanceResult:
    """Diagnose (if needed) -> de-clip -> upsample -> master -> export 24-bit WAV."""
    settings = get_settings()
    profile = settings.profile(profile_name)
    started = time.perf_counter()
    stage = on_stage or (lambda name: None)           # the worker uses this to report progress

    if audio is None:
        audio = load_audio(input_path)
    if report is None:
        stage("diagnosing")
        report = analyze(audio=audio, profile_name=profile.name)

    device = pick_device()
    target_sr = settings.target_sample_rate
    needs_repair = report.flags.declip or report.flags.upsample
    result = EnhanceResult(
        Path(output_path), describe_device(device),
        ai_bypassed=not needs_repair,
        outcome="no_signal" if not report.has_signal else ("repaired" if needs_repair else "bypassed"),
        loudness_profile=profile.name, loudness_profile_label=profile.label,
        target_lufs=profile.target_lufs, ceiling_dbtp=profile.ceiling_dbtp,
        output_sample_rate=target_sr, input_lufs=report.loudness.integrated_lufs,
    )
    try:
        samples, sr = audio.samples, audio.sample_rate
        result.input_true_peak_dbtp = measure_true_peak(samples) if report.has_signal else None

        # 1. De-clip, only if the gatekeeper found clipping
        if report.flags.declip:
            stage("enhancing")
            samples, repaired = declip(samples, threshold=settings.clip_threshold)
            result.add_step("declip", "applied", "Rebuilt the flattened peaks",
                            f"declip: {repaired} flat tops repaired (cubic spline)")
        else:
            result.add_step("declip", "skipped", "No clipping to repair")

        # 2. Upsample: AI bandwidth extension if flagged, otherwise plain resampling
        if report.flags.upsample:
            stage("enhancing")
            up = bandwidth_extend(samples, sr, report.bandwidth.cutoff_hz, device, target_sr)
            samples, sr = up.samples, up.sample_rate
            result.upsample_method = up.method
            result.notes.append(up.note)
            if up.method == "audiosr":
                result.highs_restored = True
                result.add_step("bandwidth_extension", "applied", "Restored the missing high frequencies with AI",
                                f"AudioSR above {report.bandwidth.cutoff_hz:.0f} Hz, {audio.sample_rate} -> {sr} Hz")
            else:
                result.outcome = "partial"
                result.add_step("bandwidth_extension", "fallback",
                                f"Converted to {sr / 1000:g} kHz without restoring the missing highs",
                                f"resample_only {audio.sample_rate} -> {sr} Hz (torchaudio sinc/Kaiser)")
        else:
            result.add_step("bandwidth_extension", "skipped", "High frequencies were already complete")
            if sr != target_sr:
                samples, sr = resample(samples, sr, target_sr, device), target_sr
                result.add_step("resample", "applied", f"Converted to {target_sr / 1000:g} kHz",
                                f"resample {audio.sample_rate} -> {target_sr} Hz (torchaudio sinc/Kaiser)")

        # 3. Master: every track, AI or not
        stage("mastering")
        mastered = master(samples, sr, profile.target_lufs, profile.ceiling_dbtp, profile.loud_ceiling_dbtp)
        result.ceiling_dbtp = mastered.ceiling_dbtp
        if mastered.output_lufs is None:
            result.add_step("master", "skipped", "Loudness left unchanged: the track is silent or too short")
        else:
            target_text = ("kept the original loudness" if profile.target_lufs is None
                           else f"set loudness to {profile.target_lufs:g} LUFS")
            result.add_step("master", "applied",
                            f"Mastered for {profile.label}: {target_text}, peaks at or below "
                            f"{mastered.ceiling_dbtp:g} dBTP",
                            f"gain {mastered.gain_db:+.1f} dB, limiter {mastered.limiter_reduction_db:.1f} dB, "
                            f"true peak {mastered.true_peak_dbtp:.2f} dBTP (16x oversampled)")
            if profile.target_lufs is not None and mastered.output_lufs < profile.target_lufs - 0.5:
                result.notes.append(f"Kept at {mastered.output_lufs:.1f} LUFS to protect the peaks: reaching "
                                    f"{profile.target_lufs:g} LUFS would need heavy limiting.")

        # 4. Export, and measure what we deliver
        export_wav(mastered.samples, sr, result.output_path)
        out_audio = AudioData(mastered.samples, sr)
        out_bw = measure_bandwidth(out_audio, cutoff_limit_hz=settings.rolloff_cutoff_hz)
        result.output_metrics = OutputMetrics(
            clipped_ratio=detect_clipping(out_audio, threshold=settings.clip_threshold).clipped_ratio,
            cutoff_hz=out_bw.cutoff_hz, nyquist_hz=out_bw.nyquist_hz)
        result.output_lufs = mastered.output_lufs
        result.output_true_peak_dbtp = mastered.true_peak_dbtp
        result.limiter_reduction_db = mastered.limiter_reduction_db
        result.mastered = mastered.samples
    finally:
        if device.type == "cuda":
            torch.cuda.empty_cache()                  # hand GPU memory back for the next job

    result.processing_seconds = round(time.perf_counter() - started, 2)
    return result
