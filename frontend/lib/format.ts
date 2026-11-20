// Small pure helpers for numbers on screen (kept free of React so they're easy to test).

export const FULL_RANGE_HZ = 19_500; // above this, missing highs are inaudible to almost everyone

/** "3:07" from seconds, for a running playhead (counts whole seconds that have passed). */
export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** "3:07" for a track length. Rounds the total first, so 59.6 s is "1:00", never "0:60". */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Spoken form for screen readers: "1 minute 2 seconds". */
export function spokenTime(seconds: number): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const m = Math.floor(total / 60);
  const s = total % 60;
  const parts: string[] = [];
  if (m) parts.push(`${m} minute${m === 1 ? "" : "s"}`);
  parts.push(`${s} second${s === 1 ? "" : "s"}`);
  return parts.join(" ");
}

export function kHz(hz: number): string {
  return `${(hz / 1000).toFixed(1)} kHz`;
}

/** "Full range" once the highs reach the top of human hearing, else "Up to 16.0 kHz". */
export function highFrequencies(cutoffHz: number): string {
  return cutoffHz >= FULL_RANGE_HZ ? "Full range" : `Up to ${kHz(cutoffHz)}`;
}

export function db(value: number | null | undefined, unit: string, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "Not measured"
    : `${value.toFixed(digits)} ${unit}`;
}

export function percent(ratio: number): string {
  if (ratio === 0) return "0%";
  if (ratio < 0.0001) return "< 0.01%";
  return `${(ratio * 100).toFixed(2)}%`;
}

/** Level matching: turn the louder version down so "louder" isn't mistaken for "better". */
export function matchedGains(
  on: boolean,
  originalLufs: number | null,
  enhancedLufs: number | null,
): { original: number; enhanced: number } {
  if (!on || originalLufs === null || enhancedLufs === null) return { original: 1, enhanced: 1 };
  const difference = originalLufs - enhancedLufs; // > 0: original is louder
  return difference > 0
    ? { original: 10 ** (-difference / 20), enhanced: 1 }
    : { original: 1, enhanced: 10 ** (difference / 20) };
}
