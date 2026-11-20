// Before -> after, in four numbers everyone can follow. Honest when something wasn't improved.
import { db, highFrequencies, percent } from "@/lib/format";
import type { Diagnostics, Processing } from "@/lib/types";

import dash from "./Dashboard.module.css";
import styles from "./Report.module.css";

const NOT_RESTORED = "High frequencies were not restored: ";

export default function MetricsStrip({ diagnostics: d, processing: p }: { diagnostics: Diagnostics; processing: Processing }) {
  const out = p.output_metrics;
  const reason = p.notes.find((n) => n.startsWith(NOT_RESTORED))?.slice(NOT_RESTORED.length);
  const highsMissing = d.flags.upsample && !p.highs_restored;
  const rows = [
    {
      label: "Loudness",
      before: db(d.loudness.integrated_lufs, "LUFS"),
      after: db(p.output_lufs, "LUFS"),
      note: p.target_lufs === null ? "Kept as it was" : `Target ${p.target_lufs} LUFS`,
      warn: false,
    },
    {
      label: "True peak",
      before: db(p.input_true_peak_dbtp, "dBTP"),
      after: db(p.output_true_peak_dbtp, "dBTP"),
      note: `Ceiling ${p.ceiling_dbtp} dBTP`,
      warn: false,
    },
    {
      label: "Clipping",
      before: percent(d.clipping.clipped_ratio),
      after: out ? percent(out.clipped_ratio) : "Not measured",
      note: "Share of flattened samples",
      warn: false,
    },
    {
      label: "High frequencies",
      before: highFrequencies(d.bandwidth.cutoff_hz),
      // When the highs were missing and not restored, any energy measured up there afterwards is a
      // by-product of de-clipping, not restored music: say "Not restored" rather than show it.
      after: highsMissing ? "Not restored" : out ? highFrequencies(out.cutoff_hz) : "Not measured",
      note: highsMissing ? `Not restored: ${reason ?? "the AI model is unavailable"}` : "Highest frequency with real content",
      warn: highsMissing,
    },
  ];

  return (
    <section className={dash.panel} aria-labelledby="metrics-title">
      <h2 id="metrics-title" className={dash.panelTitle}>
        Before and after
      </h2>
      <dl className={styles.strip}>
        {rows.map((row) => (
          <div key={row.label} className={styles.metric}>
            <dt>{row.label}</dt>
            <dd>
              <span className={styles.before}>
                <span className="visually-hidden">Before: </span>
                {row.before}
              </span>
              <span className={styles.arrow} aria-hidden="true">
                →
              </span>
              <span className={styles.after}>
                <span className="visually-hidden">After: </span>
                {row.after}
              </span>
            </dd>
            <dd className={`${styles.metricNote} ${row.warn ? styles.warnText : ""}`}>{row.note}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
