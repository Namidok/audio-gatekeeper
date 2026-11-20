"""Run the full pipeline on one file.  Usage: python enhance_file.py input.mp3 output.wav"""
import logging
import sys

from app.ai_pipeline import enhance
from app.dsp_diagnostics import analyze

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
source, target = sys.argv[1], sys.argv[2]

before = analyze(source)
result = enhance(source, target, report=before, on_stage=lambda s: print(f"  ... {s}"))
after = analyze(target)

print(f"\n{source}")
print(f"  Health      : {before.health_score} ({before.grade}) -> {after.health_score} ({after.grade})")
print(f"  AI bypassed : {result.ai_bypassed}")
print(f"  Applied     : {'; '.join(result.steps_applied)}")
print(f"  Skipped     : {'; '.join(result.steps_skipped) or '-'}")
for note in result.notes:
    print(f"  Note        : {note}")
lufs = f"{result.output_lufs:.1f}" if result.output_lufs is not None else "n/a"
print(f"  Output      : {lufs} LUFS, {result.output_true_peak_dbtp:.1f} dBTP, "
      f"{result.output_sample_rate} Hz / {result.output_bit_depth}-bit, on {result.device}, "
      f"{result.processing_seconds} s")
print(f"  Saved to    : {result.output_path}")
