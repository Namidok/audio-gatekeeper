"""Benchmark the pipeline on a folder of audio files and report MEASURED before/after numbers.

    cd backend
    python scripts/benchmark.py --synthetic                 # generate the synthetic set and run it
    python scripts/benchmark.py path/to/folder             # your own files (only audio you have rights to)
    python scripts/benchmark.py path/to/folder --json out.json --markdown out.md

Runs locally: no S3, no Redis, no API. Every number is measured on the actual input and output.
The "after" health score is the gatekeeper run again on the mastered 24-bit WAV.
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


def synthetic_set(folder: Path, seconds: float = 30.0) -> list[Path]:
    """Deterministic test signals with known damage (no copyrighted audio)."""
    import soundfile as sf

    from tests.signals import hard_clip, lowpass, music

    folder.mkdir(parents=True, exist_ok=True)
    sr = 44_100
    # Pink-noise tilt and moderate transients: about 13 dB peak-to-loudness, like a typical mix
    base = music(seconds=seconds, sr=sr, peak_dbfs=-1.0, seed=1, slope=0.5, crest=0.5)
    cases = {
        "01_clean_master": (base, sr),
        "02_hard_clipped_+6dB": (hard_clip(base, 6), sr),
        "03_clipped_then_turned_down": (hard_clip(base, 6) * np.float32(0.708), sr),
        "04_mp3_like_cutoff_16k": (lowpass(base, sr, 16_000), sr),
        "05_mp3_like_cutoff_11k": (lowpass(base, sr, 11_000), sr),
        "06_clipped_and_cutoff_11k": (hard_clip(lowpass(base, sr, 11_000), 6), sr),
        "07_quiet_-30dBFS_peak": (base * np.float32(10 ** (-29 / 20)), sr),
        "08_low_sample_rate_22k": (music(seconds=seconds, sr=22_050, peak_dbfs=-1.0, seed=2, slope=0.5, crest=0.5), 22_050),
        "09_silence": (np.zeros((int(seconds * sr), 2), np.float32), sr),
    }
    paths = []
    for name, (x, rate) in cases.items():
        path = folder / f"{name}.wav"
        sf.write(str(path), x, rate, subtype="PCM_24")
        paths.append(path)
    return paths


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
        "processing_s": round(seconds, 2),
        "realtime_factor": round(before.duration_seconds / seconds, 1) if seconds else None,
        "device": result.device,
    }


def fmt(v, digits=1):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def markdown(rows: list[dict], env: dict) -> str:
    lines = [
        "| File | Score before → after | Flags → outcome | LUFS before → after | True peak (dBTP) before → after "
        "| Clipping % before → after | Cutoff (kHz) before → after | Time (s) | × real time |",
        "|" + "---|" * 9,
    ]
    for r in rows:
        lines.append(
            f"| {r['file']} | {r['score_before']} {r['grade_before']} → {r['score_after']} {r['grade_after']} "
            f"| {r['flags']} → {r['outcome']} ({r['upsample_method']}) "
            f"| {fmt(r['lufs_before'])} → {fmt(r['lufs_after'])} "
            f"| {fmt(r['true_peak_before_dbtp'], 2)} → {fmt(r['true_peak_after_dbtp'], 2)} "
            f"| {fmt(r['clipping_before_pct'], 3)} → {fmt(r['clipping_after_pct'], 3)} "
            f"| {r['cutoff_before_hz'] / 1000:.1f} → {r['cutoff_after_hz'] / 1000:.1f} "
            f"| {fmt(r['processing_s'], 2)} | {fmt(r['realtime_factor'])} |"
        )
    lines.append("")
    lines.append(f"Environment: {env['machine']}, Python {env['python']}, device {env['device']}, "
                 f"profile {env['profile']}, AudioSR available: {env['audiosr']}.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", type=Path, help="folder of audio files")
    parser.add_argument("--synthetic", action="store_true", help="generate and run the synthetic test set")
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

    if args.synthetic:
        files = synthetic_set(BACKEND / "scripts" / "samples" / "benchmark")
    elif args.folder:
        files = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    else:
        parser.error("give a folder or --synthetic")
    if not files:
        parser.error("no audio files found")

    rows = []
    for path in files:
        print(f"  {path.name} ...", file=sys.stderr, flush=True)
        try:
            rows.append(measure(path, args.profile))
        except Exception as exc:  # report and continue with the next file
            print(f"    skipped: {exc.__class__.__name__}: {exc}", file=sys.stderr)

    env = {
        "machine": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "device": describe_device(pick_device()),
        "profile": config.get_settings().profile(args.profile).label,
        "audiosr": audiosr_available(),
    }
    table = markdown(rows, env)
    print(table)
    if args.markdown:
        args.markdown.write_text(table + "\n")
    if args.json:
        args.json.write_text(json.dumps({"environment": env, "results": rows}, indent=2))


if __name__ == "__main__":
    main()
