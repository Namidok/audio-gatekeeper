"use client";

// Live spectrum analyser: bass on the left, treble on the right, measured from whichever
// version is playing. It only animates while something plays (no idle 60 fps loop).
import { useEffect, useRef } from "react";

interface Props {
  analyser: AnalyserNode | null;
  colorVar: string; // CSS variable with the bar colour, e.g. "--enhanced"
}

const BARS = 64;
const LOWEST_HZ = 30;
const LABELS = [100, 1000, 5000, 10000, 20000];
const HEIGHT = 140;

export default function Spectrum({ analyser, colorVar }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let frame = 0;
    const levels = new Float32Array(BARS); // what's on screen, for smooth falling
    const data = analyser ? new Uint8Array(analyser.frequencyBinCount) : null; // reused every frame

    function render() {
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const css = getComputedStyle(canvas);
      const color = css.getPropertyValue(colorVar).trim() || "#6db8ff";
      const labelColor = css.getPropertyValue("--muted").trim() || "#8e9bae";

      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const labelSpace = 16;
      const plotHeight = height - labelSpace;
      const nyquist = analyser ? analyser.context.sampleRate / 2 : 24000;
      const position = (hz: number) => Math.log(hz / LOWEST_HZ) / Math.log(nyquist / LOWEST_HZ);
      if (analyser && data) analyser.getByteFrequencyData(data);

      const barWidth = width / BARS;
      let moving = false;
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.9;
      for (let i = 0; i < BARS; i++) {
        // Logarithmic spacing, like our hearing: as much room for 100-200 Hz as for 10-20 kHz
        const lowHz = LOWEST_HZ * (nyquist / LOWEST_HZ) ** (i / BARS);
        const highHz = LOWEST_HZ * (nyquist / LOWEST_HZ) ** ((i + 1) / BARS);
        let target = 0;
        if (data) {
          const binHz = nyquist / data.length;
          const from = Math.floor(lowHz / binHz);
          const to = Math.max(from + 1, Math.ceil(highHz / binHz));
          for (let b = from; b < to && b < data.length; b++) target = Math.max(target, data[b]);
          target /= 255;
        }
        levels[i] = target > levels[i] ? target : levels[i] * 0.92; // rise instantly, fall gently
        if (levels[i] > 0.002) moving = true;
        const h = levels[i] * plotHeight;
        ctx.fillRect(i * barWidth + 1, plotHeight - h, Math.max(1, barWidth - 2), h);
      }

      // Frequency labels along the bottom
      ctx.fillStyle = labelColor;
      ctx.font = "10px system-ui, sans-serif";
      let labelEnd = -Infinity; // right edge of the last label drawn, so narrow screens skip overlaps
      for (const hz of LABELS) {
        if (hz > nyquist) continue;
        const x = position(hz) * width;
        ctx.globalAlpha = 0.25;
        ctx.fillRect(x, 0, 1, plotHeight);
        const text = hz >= 1000 ? `${hz / 1000}k` : `${hz}`;
        const textWidth = ctx.measureText(text).width;
        const textX = x + 3 + textWidth > width ? x - textWidth - 3 : x + 3; // keep "20k" inside
        if (textX < labelEnd + 4) continue;
        ctx.globalAlpha = 0.8;
        ctx.fillText(text, textX, height - 3);
        labelEnd = textX + textWidth;
      }
      ctx.globalAlpha = 1;

      // Keep animating while audio plays, or until the bars have fallen after it stopped
      if (analyser || moving) frame = requestAnimationFrame(render);
    }

    render();
    return () => cancelAnimationFrame(frame);
  }, [analyser, colorVar]);

  return <canvas ref={canvasRef} aria-hidden="true" style={{ width: "100%", height: HEIGHT, display: "block" }} />;
}
