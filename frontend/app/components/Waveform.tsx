"use client";

// One version's waveform with a playhead. Click, or use the arrow / Home / End keys, to jump.
import { useEffect, useRef } from "react";

import { spokenTime } from "@/lib/format";

interface Props {
  peaks: number[] | null; // 0..1, from the backend
  progress: number; // 0..1, how far this version has played
  duration: number; // seconds (0 until known)
  colorVar: string; // CSS variable holding the lane colour, e.g. "--original"
  dimmed: boolean; // true for the version you're not listening to
  label: string;
  onSeek: (fraction: number) => void;
}

const HEIGHT = 72;

export default function Waveform({ peaks, progress, duration, colorVar, dimmed, label, onSeek }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Redraw when the data, the position, the size or the colour theme changes
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    function draw() {
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const css = getComputedStyle(canvas);
      const color = css.getPropertyValue(colorVar).trim() || "#6db8ff";
      const clipColor = css.getPropertyValue("--crit").trim() || "#ff6b6b";
      const headColor = css.getPropertyValue("--text").trim() || "#ffffff";
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const middle = height / 2;
      const values = peaks && peaks.length ? peaks : [0];
      const playedX = progress * width;

      // One thin bar per pixel column, mirrored above and below the middle line
      ctx.fillStyle = color;
      for (let x = 0; x < width; x++) {
        const peak = values[Math.floor((x / width) * values.length)] ?? 0;
        const bar = Math.max(1, peak * (height / 2 - 2));
        ctx.globalAlpha = (x <= playedX ? 1 : 0.35) * (dimmed ? 0.5 : 1);
        ctx.fillRect(x, middle - bar, 1, bar * 2);
      }

      // The full-scale lines: peaks touching them may have been clipped
      ctx.globalAlpha = 0.3;
      ctx.fillStyle = clipColor;
      ctx.fillRect(0, 1, width, 1);
      ctx.fillRect(0, height - 2, width, 1);

      // Playhead
      ctx.globalAlpha = dimmed ? 0.5 : 1;
      ctx.fillStyle = headColor;
      ctx.fillRect(Math.min(playedX, width - 2), 0, 2, height);
      ctx.globalAlpha = 1;
    }

    draw();
    const resize = new ResizeObserver(draw);
    resize.observe(canvas);
    const theme = new MutationObserver(draw);
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const scheme = window.matchMedia("(prefers-color-scheme: light)");
    scheme.addEventListener("change", draw);
    return () => {
      resize.disconnect();
      theme.disconnect();
      scheme.removeEventListener("change", draw);
    };
  }, [peaks, progress, colorVar, dimmed]);

  function seekFromPointer(e: React.MouseEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    onSeek(Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)));
  }

  function seekFromKeys(e: React.KeyboardEvent) {
    const step = duration ? 5 / duration : 0.02; // 5 seconds
    const moves: Record<string, number> = {
      ArrowRight: progress + step,
      ArrowUp: progress + step,
      ArrowLeft: progress - step,
      ArrowDown: progress - step,
      PageUp: progress + step * 6,
      PageDown: progress - step * 6,
      Home: 0,
      End: 0.999,
    };
    if (e.key in moves) {
      e.preventDefault();
      onSeek(Math.min(1, Math.max(0, moves[e.key])));
    }
  }

  return (
    <div>
      <canvas
        ref={canvasRef}
        onClick={seekFromPointer}
        onKeyDown={seekFromKeys}
        tabIndex={0}
        role="slider"
        aria-label={`${label} position`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
        aria-valuetext={duration ? `${spokenTime(progress * duration)} of ${spokenTime(duration)}` : "Not loaded yet"}
        style={{ width: "100%", height: HEIGHT, display: "block", cursor: "pointer", borderRadius: 4 }}
      />
      {!peaks && (
        <p style={{ fontSize: "0.75rem", color: "var(--muted)", marginTop: 4 }}>
          Waveform picture unavailable for this job. Playback and seeking still work.
        </p>
      )}
    </div>
  );
}
