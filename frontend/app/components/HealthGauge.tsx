// The radial health score, drawn like a meter. The arc sweeps once when results arrive.
import type { Diagnostics, Processing } from "@/lib/types";

import styles from "./Report.module.css";

const CX = 100;
const CY = 100;
const R = 80;
const START = 135; // degrees, clockwise from 3 o'clock: bottom-left
const SPAN = 270;

function point(angleDeg: number, radius: number) {
  const a = (angleDeg * Math.PI) / 180;
  return { x: CX + radius * Math.cos(a), y: CY + radius * Math.sin(a) };
}

const START_POINT = point(START, R);
const END_POINT = point(START + SPAN, R);
const ARC = `M ${START_POINT.x.toFixed(2)} ${START_POINT.y.toFixed(2)} A ${R} ${R} 0 1 1 ${END_POINT.x.toFixed(2)} ${END_POINT.y.toFixed(2)}`;
const TICKS = Array.from({ length: 11 }, (_, i) => {
  const angle = START + (SPAN * i) / 10;
  return { inner: point(angle, R - 14), outer: point(angle, R - (i % 5 === 0 ? 6 : 9)) };
});

function verdict(d: Diagnostics): string {
  if (!d.has_signal) return "No signal";
  if (d.health_score >= 90) return "Healthy";
  if (d.health_score >= 65) return "Needs attention";
  return "Damaged";
}

/** One plain sentence about what was found and done. */
function summary(d: Diagnostics, p: Processing | null): string {
  if (!d.has_signal || p?.outcome === "no_signal") return "The track is silent or too short, so nothing was changed.";
  const damage = [d.flags.declip && "clipping", d.flags.upsample && "missing high frequencies"].filter(Boolean) as string[];
  if (!p) return damage.length ? `Found ${damage.join(" and ")}. Repairing now.` : "No damage found, so repair will be skipped.";
  if (p.outcome === "bypassed") return "No damage found, so repair was skipped. Only loudness and peaks were adjusted.";
  const done = [
    p.steps.some((s) => s.kind === "declip" && s.status === "applied") && "flattened peaks were rebuilt",
    p.highs_restored && "missing high frequencies were restored",
  ].filter(Boolean) as string[];
  const sentence = done.length ? `Repaired: ${done.join(", and ")}.` : "";
  if (p.outcome === "partial") return `${sentence} The missing high frequencies were not restored (see the notes).`.trim();
  return sentence || "Repaired and mastered.";
}

export default function HealthGauge({ diagnostics: d, processing: p }: { diagnostics: Diagnostics; processing: Processing | null }) {
  const score = d.has_signal ? d.health_score : 0;
  const tone = !d.has_signal ? "crit" : score >= 80 ? "ok" : score >= 50 ? "warn" : "crit";

  return (
    <div className={styles.gauge}>
      <svg
        viewBox="0 0 200 172"
        className={styles.dial}
        role="img"
        aria-label={`Health score ${score} out of 100, grade ${d.grade}: ${verdict(d)}`}
      >
        <path d={ARC} className={styles.track} pathLength={100} />
        <path
          d={ARC}
          className={`${styles.value} ${styles[tone]}`}
          pathLength={100}
          strokeDasharray="100"
          style={{ strokeDashoffset: 100 - score }}
        />
        {TICKS.map((t, i) => (
          <line key={i} x1={t.inner.x} y1={t.inner.y} x2={t.outer.x} y2={t.outer.y} className={styles.tick} />
        ))}
        <text x={CX} y={CY + 8} textAnchor="middle" className={styles.score}>
          {score}
        </text>
        <text x={CX} y={CY + 34} textAnchor="middle" className={styles.grade}>
          Grade {d.grade}
        </text>
      </svg>
      <p className={`${styles.verdict} ${styles[tone]}`}>{verdict(d)}</p>
      <p className={styles.summary}>{summary(d, p)}</p>
    </div>
  );
}
