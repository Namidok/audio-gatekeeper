"""The DSP gatekeeper: measures a track and decides whether it needs repair.

Clipping, crest factor, bandwidth (spectral cliff + 85% rolloff) and BS.1770 loudness are
combined into a 0-100 health score and two action flags (declip / upsample). Tracks with no
flags bypass the AI stage completely.
"""
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf

from app.config import get_settings
from app.errors import UserInputError

Severity = Literal["info", "warning", "critical"]
Grade = Literal["A", "B", "C", "D", "F"]

QUIETEST_CLIPPABLE_PEAK = 0.1   # files peaking below -20 dBFS are not checked for clipping


@dataclass
class AudioData:
    samples: np.ndarray   # shape: (num_samples, num_channels), values from -1.0 to 1.0
    sample_rate: int      # samples per second, e.g. 44100

    @property
    def channels(self) -> int:
        return self.samples.shape[1]

    @property
    def duration_seconds(self) -> float:
        return self.samples.shape[0] / self.sample_rate

    @property
    def mono(self) -> np.ndarray:
        # Average the channels into one signal (used for frequency analysis)
        return self.samples.mean(axis=1)


def check_limits(path: str | Path):
    """Read only the header and refuse files that would be too big to decode safely."""
    s = get_settings()
    try:
        info = sf.info(str(path))
    except Exception as exc:  # libsndfile raises several types for unreadable files
        raise UserInputError("This file couldn't be read as audio. It may be damaged, "
                             "or not really the format its name suggests.", "unreadable") from exc
    if info.frames <= 0 or info.samplerate <= 0:
        raise UserInputError("This file contains no audio.", "empty")
    if info.channels > s.max_channels:
        limit = "only mono or stereo is supported" if s.max_channels == 2 else f"the limit is {s.max_channels}"
        raise UserInputError(f"The file has {info.channels} channels; {limit}.", "too_many_channels")
    if info.samplerate > s.max_sample_rate:
        raise UserInputError(f"Sample rate {info.samplerate} Hz is above the {s.max_sample_rate} Hz limit.",
                             "sample_rate_too_high")
    duration = info.frames / info.samplerate
    if duration > s.max_duration_seconds:
        raise UserInputError(f"The track is {duration / 60:.0f} minutes long; the limit is "
                             f"{s.max_duration_seconds / 60:.0f} minutes.", "too_long")
    return info


def load_audio(path: str | Path) -> AudioData:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")
    check_limits(path)

    try:
        samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        raise UserInputError("This file couldn't be decoded. It may be damaged.", "undecodable") from exc

    if samples.shape[0] == 0:
        raise UserInputError("This file contains no audio.", "empty")
    if not np.isfinite(samples).all():
        raise UserInputError("The audio contains invalid (NaN or infinite) values.", "invalid_samples")

    return AudioData(samples=samples, sample_rate=sample_rate)


# --------------------------------------------------------------------------- #
# Clipping
# --------------------------------------------------------------------------- #
@dataclass
class ClippingResult:
    clipped_samples: int    # how many samples sit on a flat top
    clipped_ratio: float    # clipped_samples / all samples (0.0 to 1.0)
    clip_events: int        # how many separate flat tops were found
    clip_level: float       # the level the flat tops are looked for at (fraction of full scale)
    is_clipped: bool        # the yes/no decision


def _find_runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return start and end (exclusive) positions of every run of True values in mask."""
    padded = np.concatenate(([0], mask.astype(np.int8), [0]))
    edges = np.diff(padded)
    return np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)


def flat_top_runs(channel: np.ndarray, level: float, min_run: int = 3,
                  tolerance: float = 1e-5) -> list[tuple[int, int]]:
    """(start, end) of every run of >= min_run samples at or above `level` that stay flat.

    One rule shared by the detector and the de-clipper, so they always agree.
    """
    loud = np.abs(channel) >= level
    flat_step = np.abs(np.diff(channel)) <= tolerance
    same_as_prev = np.concatenate(([False], flat_step))
    same_as_next = np.concatenate((flat_step, [False]))
    starts, ends = _find_runs(loud & (same_as_prev | same_as_next))
    keep = (ends - starts) >= min_run
    return list(zip(starts[keep].tolist(), ends[keep].tolist()))


def clip_level(samples: np.ndarray, threshold: float) -> float | None:
    """Flat tops are looked for near the file's OWN peak, not only near full scale.

    This also catches audio that was clipped and then turned down (a very common case that an
    absolute 0.99 threshold misses). Returns None for files too quiet to have been clipped.
    """
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < QUIETEST_CLIPPABLE_PEAK:
        return None
    return threshold * peak


def detect_clipping(audio: AudioData, threshold: float = 0.99, min_run: int = 3,
                    tolerance: float = 1e-5, max_ratio: float = 0.0001) -> ClippingResult:
    level = clip_level(audio.samples, threshold)
    if level is None:
        return ClippingResult(0, 0.0, 0, 0.0, False)

    total_clipped = 0
    total_events = 0
    for ch in range(audio.channels):          # check each channel separately
        # "flat" is judged relative to the level, so turned-down audio is held to the same standard
        runs = flat_top_runs(audio.samples[:, ch], level, min_run, tolerance * level)
        total_clipped += sum(end - start for start, end in runs)
        total_events += len(runs)

    ratio = total_clipped / audio.samples.size
    return ClippingResult(
        clipped_samples=total_clipped,
        clipped_ratio=ratio,
        clip_events=total_events,
        clip_level=round(level, 4),
        is_clipped=ratio > max_ratio,
    )


# --------------------------------------------------------------------------- #
# Dynamics
# --------------------------------------------------------------------------- #
@dataclass
class DynamicsResult:
    peak_dbfs: float        # loudest single sample, in dB (0 dB = full scale)
    rms_dbfs: float         # average power level, in dB
    crest_factor_db: float  # peak_dbfs - rms_dbfs
    is_squashed: bool       # True if crest factor is below the limit


def to_db(value: float) -> float:
    return float(20.0 * np.log10(max(value, 1e-10)))   # avoid log10(0) = -infinity


def measure_dynamics(audio: AudioData, min_crest_db: float = 6.0) -> DynamicsResult:
    samples = audio.samples.astype(np.float64)    # extra precision for the maths
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples ** 2)))

    if rms < 1e-6:                                # silent file: nothing to judge
        return DynamicsResult(to_db(peak), to_db(rms), 0.0, False)

    peak_db = to_db(peak)
    rms_db = to_db(rms)
    crest_db = peak_db - rms_db
    return DynamicsResult(
        peak_dbfs=peak_db,
        rms_dbfs=rms_db,
        crest_factor_db=crest_db,
        is_squashed=crest_db < min_crest_db,
    )


# --------------------------------------------------------------------------- #
# Bandwidth: spectral cliff (the decision) + 85% rolloff (reported only)
# --------------------------------------------------------------------------- #
@dataclass
class BandwidthResult:
    rolloff_85_hz: float          # frequency below which 85% of the energy sits (informational)
    cutoff_hz: float              # where the real high-frequency content stops
    nyquist_hz: float             # highest frequency this sample rate can hold
    is_band_limited: bool         # True if cutoff_hz is below the limit
    limited_by_sample_rate: bool  # True if the sample rate itself is what caps the highs


def _band_levels_db(power: np.ndarray, freqs: np.ndarray, band_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """Average power in fixed-width bands, in dB relative to the loudest band."""
    edges = np.arange(0.0, freqs[-1] + band_hz, band_hz)
    levels = []
    for low, high in zip(edges[:-1], edges[1:]):
        in_band = (freqs >= low) & (freqs < high)
        levels.append(power[in_band].mean() if in_band.any() else 0.0)
    levels = np.array(levels)
    loudest = max(levels.max(), 1e-20)
    return edges[1:], 10.0 * np.log10(levels / loudest + 1e-20)


def measure_bandwidth(audio: AudioData, rolloff_percent: float = 0.85,
                      cutoff_limit_hz: float = 15_000, band_hz: float = 500,
                      cliff_db: float = 20.0, search_from_hz: float = 5_000) -> BandwidthResult:
    sr = audio.sample_rate
    nyquist = sr / 2

    # 1. Spectrogram: how strong each frequency is, frame by frame
    magnitude = np.abs(librosa.stft(audio.mono, n_fft=4096, hop_length=1024))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)

    # 2. The spec's 85% rolloff (kept as an informational metric: it flags most normal music)
    rolloff = librosa.feature.spectral_rolloff(S=magnitude, sr=sr, roll_percent=rolloff_percent)
    rolloff_hz = float(np.median(rolloff))

    # 3. Find the "cliff": a sudden drop of cliff_db within ~1 kHz (what MP3 encoders leave behind)
    band_tops, levels_db = _band_levels_db(np.mean(magnitude ** 2, axis=1), freqs, band_hz)
    look_ahead = int(1000 / band_hz)                      # compare with the band 1 kHz higher
    drops = levels_db[:-look_ahead] - levels_db[look_ahead:]
    candidates = np.flatnonzero((drops >= cliff_db) & (band_tops[:-look_ahead] >= search_from_hz))
    cutoff_hz = float(band_tops[candidates.max()]) if candidates.size else nyquist
    cutoff_hz = min(cutoff_hz, nyquist)

    is_band_limited = cutoff_hz < cutoff_limit_hz
    return BandwidthResult(
        rolloff_85_hz=rolloff_hz,
        cutoff_hz=cutoff_hz,
        nyquist_hz=nyquist,
        is_band_limited=is_band_limited,
        limited_by_sample_rate=is_band_limited and cutoff_hz >= nyquist - band_hz,
    )


# --------------------------------------------------------------------------- #
# Loudness (ITU-R BS.1770 via pyloudnorm)
# --------------------------------------------------------------------------- #
@dataclass
class LoudnessResult:
    integrated_lufs: float | None   # None if the audio is silent or too short
    gain_to_target_db: float | None # how much mastering would raise/lower it (None: no target)


def measure_loudness(audio: AudioData, target_lufs: float | None = -14.0) -> LoudnessResult:
    meter = pyln.Meter(audio.sample_rate)          # ITU-R BS.1770 meter, 400 ms blocks
    if audio.duration_seconds < meter.block_size:
        return LoudnessResult(None, None)          # shorter than one measuring block

    lufs = meter.integrated_loudness(audio.samples.astype(np.float64))
    if not np.isfinite(lufs):                      # silence comes back as -inf
        return LoudnessResult(None, None)

    return LoudnessResult(
        integrated_lufs=float(lufs),
        gain_to_target_db=None if target_lufs is None else float(target_lufs - lufs),
    )


# --------------------------------------------------------------------------- #
# Health score + the main analyze() entry point
# --------------------------------------------------------------------------- #
@dataclass
class ActionFlags:
    upsample: bool = False   # run AI bandwidth extension
    declip: bool = False     # run de-clipping


@dataclass
class Issue:
    code: str          # short machine-readable id, e.g. "clipping"
    severity: Severity
    message: str       # plain-language text for the report card
    penalty: int       # points taken off the health score


@dataclass
class DiagnosticReport:
    health_score: int
    grade: Grade
    flags: ActionFlags
    sample_rate: int
    channels: int
    duration_seconds: float
    clipping: ClippingResult
    dynamics: DynamicsResult
    bandwidth: BandwidthResult
    loudness: LoudnessResult
    has_signal: bool = True
    issues: list[Issue] = field(default_factory=list)
    analysis_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _grade(score: int) -> Grade:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 65:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def _find_issues(clipping: ClippingResult, dynamics: DynamicsResult, bandwidth: BandwidthResult,
                 loudness: LoudnessResult, min_crest_db: float,
                 target_lufs: float | None) -> list[Issue]:
    issues: list[Issue] = []

    # Clipping: the more of the track is clipped, the bigger the penalty
    if clipping.is_clipped:
        pct = clipping.clipped_ratio * 100
        if clipping.clipped_ratio > 0.01:
            issues.append(Issue("clipping", "critical", f"Heavy clipping: the loudest moments are flattened ({pct:.2f}% of samples)", 40))
        elif clipping.clipped_ratio > 0.001:
            issues.append(Issue("clipping", "warning", f"Clipping: the loudest moments are flattened ({pct:.2f}% of samples)", 25))
        else:
            issues.append(Issue("clipping", "warning", f"Light clipping: a few peaks are flattened ({pct:.3f}% of samples)", 15))

    # Bandwidth: missing high frequencies
    cutoff_khz = bandwidth.cutoff_hz / 1000
    if bandwidth.is_band_limited:
        severity, penalty = ("critical", 40) if bandwidth.cutoff_hz < 11_000 else ("warning", 30)
        if bandwidth.limited_by_sample_rate:
            message = (f"Low sample rate: nothing above {cutoff_khz:.1f} kHz can be stored, "
                       "so the track sounds dull")
        else:
            message = (f"High frequencies are cut off at {cutoff_khz:.1f} kHz, "
                       "typical of a low-bitrate MP3")
        issues.append(Issue("band_limited", severity, message, penalty))
    elif bandwidth.cutoff_hz < min(bandwidth.nyquist_hz - 1000, 20_000):
        issues.append(Issue("lossy_source", "info",
                            f"Content stops at {cutoff_khz:.1f} kHz: likely a good-quality lossy source", 5))

    # Dynamics: squashed audio sounds flat and tiring
    if dynamics.is_squashed:
        issues.append(Issue("low_dynamics", "warning",
                            f"Very little difference between loud and quiet moments "
                            f"(crest factor {dynamics.crest_factor_db:.1f} dB, below {min_crest_db:.0f} dB)", 15))
    elif dynamics.crest_factor_db < min_crest_db + 2:
        issues.append(Issue("tight_dynamics", "info",
                            f"Fairly compressed (crest factor {dynamics.crest_factor_db:.1f} dB)", 5))

    # Loudness: mastering fixes this, so the penalty is small
    if loudness.gain_to_target_db is not None and target_lufs is not None:
        gap = abs(loudness.gain_to_target_db)
        if gap > 10:
            issues.append(Issue("loudness", "warning",
                                f"Loudness {loudness.integrated_lufs:.1f} LUFS is far from the "
                                f"{target_lufs:g} LUFS target", 10))
        elif gap > 5:
            issues.append(Issue("loudness", "info",
                                f"Loudness {loudness.integrated_lufs:.1f} LUFS will be adjusted to "
                                f"{target_lufs:g} LUFS", 5))

    return issues


def analyze(path: str | Path | None = None, *, audio: AudioData | None = None,
            profile_name: str | None = None) -> DiagnosticReport:
    """Diagnose a file (or audio that was already loaded, to avoid decoding twice)."""
    settings = get_settings()
    target_lufs = settings.profile(profile_name).target_lufs
    started = time.perf_counter()
    if audio is None:
        if path is None:
            raise ValueError("analyze() needs a path or audio")
        audio = load_audio(path)

    clipping = detect_clipping(audio, threshold=settings.clip_threshold)
    dynamics = measure_dynamics(audio, min_crest_db=settings.crest_factor_min_db)
    bandwidth = measure_bandwidth(audio, rolloff_percent=settings.rolloff_percent,
                                  cutoff_limit_hz=settings.rolloff_cutoff_hz)
    loudness = measure_loudness(audio, target_lufs=target_lufs)

    has_signal = loudness.integrated_lufs is not None
    if not has_signal:
        # Silent or too short: nothing to rescue, and nothing for the AI to work on
        issues = [Issue("no_signal", "critical", "The track is silent or too short to analyse", 100)]
        flags = ActionFlags(upsample=False, declip=False)
    else:
        issues = _find_issues(clipping, dynamics, bandwidth, loudness,
                              settings.crest_factor_min_db, target_lufs)
        flags = ActionFlags(upsample=bandwidth.is_band_limited, declip=clipping.is_clipped)

    score = max(0, min(100, 100 - sum(issue.penalty for issue in issues)))
    return DiagnosticReport(
        health_score=score,
        grade=_grade(score),
        flags=flags,
        sample_rate=audio.sample_rate,
        channels=audio.channels,
        duration_seconds=round(audio.duration_seconds, 3),
        clipping=clipping,
        dynamics=dynamics,
        bandwidth=bandwidth,
        loudness=loudness,
        has_signal=has_signal,
        issues=issues,
        analysis_seconds=round(time.perf_counter() - started, 3),
    )


if __name__ == "__main__":
    # Usage: python -m app.dsp_diagnostics some_file.wav
    print(json.dumps(analyze(sys.argv[1]).to_dict(), indent=2))
