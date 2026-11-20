"""Benchmark the pipeline on a folder of audio files and report MEASURED before/after numbers.

    cd backend
    python scripts/benchmark.py --synthetic --no-env           # generate the synthetic set and run it
    python scripts/benchmark.py path/to/folder                 # your own files (only audio you have rights to)
    python scripts/benchmark.py --synthetic --truth-pair clean.wav clipped.wav --boost-db 9 --markdown out.md

Runs locally: no S3, no Redis, no API. Every number is measured on the actual input and output.

Two extra measurements answer "did it actually change anything, and was the change right?":
  * "Changed beyond volume": the output minus a pure-volume copy of the input, in dB relative to
    the output. Around -20 dB is an obvious change, -40 dB subtle, below about -60 dB inaudible.
  * De-clipping accuracy: when the unclipped original is known (the synthetic clipped cases, or a
    --truth-pair you made by boosting and clipping a clean file), how much closer the repaired
    peaks are to the original than the clipped ones were. Negative dB = closer = better.
"""
import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff"}


def synthetic_set(folder: Path, seconds: float = 30.0) -> list[tuple[Path, np.ndarray | None]]:
    """Deterministic test signals with known damage (no copyrighted audio), with the original
    (pre-clipping) waveform for the clipped cases."""
    import soundfile as sf

    from tests.signals import hard_clip, lowpass, music

    folder.mkdir(parents=True, exist_ok=True)
    sr = 44_100
    # Pink-noise tilt and moderate transients: about 13 dB peak-to-loudness, like a typical mix
    base = music(seconds=seconds, sr=sr, peak_dbfs=-1.0, seed=1, slope=0.5, crest=0.5)
    boost6 = np.float32(10 ** (6 / 20))
    cut11 = lowpass(base, sr, 11_000)
    cases = {
        "01_clean_master": (base, sr, None),
        "02_hard_clipped_+6dB": (hard_clip(base, 6), sr, base * boost6),
        "03_clipped_then_turned_down": (hard_clip(base, 6) * np.float32(0.708), sr, base * boost6 * np.float32(0.708)),
        "04_mp3_like_cutoff_16k": (lowpass(base, sr, 16_000), sr, None),
        "05_mp3_like_cutoff_11k": (cut11, sr, None),
        "06_clipped_and_cutoff_11k": (hard_clip(cut11, 6), sr, cut11 * boost6),
        "07_quiet_-30dBFS_peak": (base * np.float32(10 ** (-29 / 20)), sr, None),
        "08_low_sample_rate_22k": (music(seconds=seconds, sr=22_050, peak_dbfs=-1.0, seed=2, slope=0.5, crest=0.5), 22_050, None),
        "09_silence": (np.zeros((int(seconds * sr), 2), np.float32), sr, None),
    }
    out = []
    for name, (x, rate, truth) in cases.items():
        path = folder / f"{name}.wav"
        sf.write(str(path), x, rate, subtype="PCM_24")
        out.append((path, truth))
    return out


def change_beyond_volume_db(original: np.ndarray, rate: int, output: np.ndarray, out_rate: int) -> float:
    """dB of (output - best pure-volume copy of the original), relative to the output."""
    from app.ai_pipeline import pick_device, resample

    ref = resample(original, rate, out_rate, pick_device("cpu")).astype(np.float64)
    out = output.astype(np.float64)
    n = min(len(ref), len(out))
    ref, out = ref[:n], out[:n]
    denom = float((ref * ref).sum())
    if denom == 0 or float((out * out).sum()) == 0:
        return float("nan")
    gain = float((out * ref).sum()) / denom
    residual = out - gain * ref
    return float(20 * np.log10(max(np.sqrt(np.mean(residual ** 2)), 1e-12) / np.sqrt(np.mean(out ** 2))))


def declip_accuracy(name: str, truth: np.ndarray, clipped: np.ndarray, rate: int) -> dict:
    """How much closer to the known original the de-clipper gets, on the samples clipping destroyed."""
    from app.ai_pipeline import declip

    n = min(len(truth), len(clipped))
    truth, clipped = truth[:n].astype(np.float64), clipped[:n].astype(np.float32)
    destroyed = np.abs(truth) > np.abs(clipped).max() * 0.999
    started = time.perf_counter()
    fixed, runs = declip(clipped)
    seconds = time.perf_counter() - started

    def error(x):
        return float(np.sqrt(np.mean((x[destroyed] - truth[destroyed]) ** 2)))

    return {
        "case": name,
        "destroyed_pct": float(destroyed.mean() * 100),
        "flat_tops": runs,
        "error_change_db": 20 * np.log10(error(fixed) / error(clipped)) if destroyed.any() else float("nan"),
        "seconds": round(seconds, 2),
        "realtime_factor": round(n / rate / seconds, 1) if seconds else None,
    }


def measure(path: Path, profile: str | None) -> dict:
    from app.ai_pipeline import enhance, measure_true_peak
    from app.dsp_diagnostics import analyze, load_audio

    audio = load_audio(path)
    before = analyze(audio=audio, profile_name=profile)
    with tempfile.TemporaryDirectory() as tmp:
        started = time.perf_counter()
        result = enhance(path, Path(tmp) / "out.wav", report=before, audio=audio, profile_name=profile)
        seconds = time.perf_counter() - started
        after = analyze(Path(tmp) / "out.wav", profile_name=profile)
    flags = [name for name, on in (("declip", before.flags.declip), ("upsample", before.flags.upsample)) if on]
    return {
        "file": path.name,
        "duration_s": round(before.duration_seconds, 1),
        "score_before": before.health_score,
        "score_after": after.health_score,
        "grade_before": before.grade,
        "grade_after": after.grade,
        "flags": "+".join(flags) or "none",
        "outcome": result.outcome,
        "upsample_method": result.upsample_method,
        "lufs_before": before.loudness.integrated_lufs,
        "lufs_after": result.output_lufs,
        "true_peak_before_dbtp": measure_true_peak(audio.samples) if before.has_signal else None,
        "true_peak_after_dbtp": result.output_true_peak_dbtp if before.has_signal else None,
        "clipping_before_pct": before.clipping.clipped_ratio * 100,
        "clipping_after_pct": after.clipping.clipped_ratio * 100,
        "cutoff_before_hz": before.bandwidth.cutoff_hz,
        "cutoff_after_hz": after.bandwidth.cutoff_hz,
        "changed_beyond_volume_db": (change_beyond_volume_db(audio.samples, audio.sample_rate, result.mastered,
                                                             result.output_sample_rate)
                                     if before.has_signal else None),
        "processing_s": round(seconds, 2),
        "realtime_factor": round(before.duration_seconds / seconds, 1) if seconds else None,
        "device": result.device,
    }


def fmt(v, digits=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def markdown(rows: list[dict], accuracy: list[dict], env: dict) -> str:
    lines = [
        "| File | Score before → after | Flags → outcome | LUFS before → after | True peak (dBTP) before → after "
        "| Clipping % before → after | Cutoff (kHz) before → after | Changed beyond volume (dB) | Time (s) | × real time |",
        "|" + "---|" * 10,
    ]
    for r in rows:
        lines.append(
            f"| {r['file']} | {r['score_before']} {r['grade_before']} → {r['score_after']} {r['grade_after']} "
            f"| {r['flags']} → {r['outcome']} ({r['upsample_method']}) "
            f"| {fmt(r['lufs_before'])} → {fmt(r['lufs_after'])} "
            f"| {fmt(r['true_peak_before_dbtp'], 2)} → {fmt(r['true_peak_after_dbtp'], 2)} "
            f"| {fmt(r['clipping_before_pct'], 3)} → {fmt(r['clipping_after_pct'], 3)} "
            f"| {r['cutoff_before_hz'] / 1000:.1f} → {r['cutoff_after_hz'] / 1000:.1f} "
            f"| {fmt(r['changed_beyond_volume_db'])} "
            f"| {fmt(r['processing_s'], 2)} | {fmt(r['realtime_factor'])} |"
        )
    if accuracy:
        lines += ["", "De-clipping accuracy against the known original (negative = closer to the original):", "",
                  "| Case | Samples destroyed by clipping | Flat tops rebuilt | Error vs original | De-clip time (s) | × real time |",
                  "|---|---|---|---|---|---|"]
        for a in accuracy:
            lines.append(f"| {a['case']} | {a['destroyed_pct']:.1f}% | {a['flat_tops']} | {fmt(a['error_change_db'])} dB "
                         f"| {fmt(a['seconds'], 2)} | {fmt(a['realtime_factor'])} |")
    lines.append("")
    lines.append(f"Environment: {env['machine']}, Python {env['python']}, device {env['device']}, "
                 f"profile {env['profile']}, AudioSR available: {env['audiosr']}.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", type=Path, help="folder of audio files")
    parser.add_argument("--synthetic", action="store_true", help="generate and run the synthetic test set")
    parser.add_argument("--truth-pair", nargs=2, type=Path, metavar=("CLEAN", "CLIPPED"),
                        help="a clean file and a version made by boosting it --boost-db and clipping")
    parser.add_argument("--boost-db", type=float, default=0.0, help="the boost used to make CLIPPED from CLEAN")
    parser.add_argument("--profile", default=None, help="spotify | youtube | preserve | custom (default: server default)")
    parser.add_argument("--json", type=Path, help="also write the results as JSON")
    parser.add_argument("--markdown", type=Path, help="also write the results as a Markdown table")
    parser.add_argument("--no-env", action="store_true", help="ignore backend/.env (defaults + environment only)")
    args = parser.parse_args()

    from app import config
    if args.no_env:
        config.Settings.model_config["env_file"] = None
        config.get_settings.cache_clear()
    from app.ai_pipeline import audiosr_available, describe_device, pick_device
    from app.dsp_diagnostics import load_audio

    files: list[tuple[Path, np.ndarray | None]] = []
    if args.synthetic:
        files += synthetic_set(BACKEND / "scripts" / "samples" / "benchmark")
    if args.folder:
        files += [(p, None) for p in sorted(args.folder.iterdir()) if p.suffix.lower() in AUDIO_EXTENSIONS]
    if not files and not args.truth_pair:
        parser.error("give a folder, --synthetic or --truth-pair")

    rows, accuracy = [], []
    for path, truth in files:
        print(f"  {path.name} ...", file=sys.stderr, flush=True)
        try:
            rows.append(measure(path, args.profile))
            if truth is not None:
                audio = load_audio(path)
                accuracy.append(declip_accuracy(path.stem, truth, audio.samples, audio.sample_rate))
        except Exception as exc:  # report and continue with the next file
            print(f"    skipped: {exc.__class__.__name__}: {exc}", file=sys.stderr)

    if args.truth_pair:
        clean_path, clipped_path = args.truth_pair
        clean, clipped = load_audio(clean_path), load_audio(clipped_path)
        print(f"  {clipped_path.name} vs {clean_path.name} ...", file=sys.stderr, flush=True)
        accuracy.append(declip_accuracy(f"{clipped_path.name} (original: {clean_path.name} +{args.boost_db:g} dB)",
                                        clean.samples * 10 ** (args.boost_db / 20), clipped.samples, clipped.sample_rate))

    env = {
        "machine": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "device": describe_device(pick_device()),
        "profile": config.get_settings().profile(args.profile).label,
        "audiosr": audiosr_available(),
    }
    table = markdown(rows, accuracy, env)
    print(table)
    if args.markdown:
        args.markdown.write_text(table + "\n")
    if args.json:
        args.json.write_text(json.dumps({"environment": env, "results": rows, "declip_accuracy": accuracy}, indent=2))


if __name__ == "__main__":
    main()
