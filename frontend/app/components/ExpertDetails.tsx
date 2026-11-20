// The raw numbers, for audio engineers. Shown when "Expert details" is switched on.
import { db, formatDuration, kHz, percent } from "@/lib/format";
import type { Job } from "@/lib/types";

import dash from "./Dashboard.module.css";
import styles from "./Report.module.css";

export default function ExpertDetails({ job }: { job: Job }) {
  const d = job.diagnostics;
  const p = job.processing;
  if (!d) return null;

  const channels = d.channels === 1 ? "mono" : d.channels === 2 ? "stereo" : `${d.channels} channels`;
  const input: [string, string][] = [
    ["Format", `${d.sample_rate} Hz, ${channels}, ${formatDuration(d.duration_seconds)}`],
    ["Integrated loudness (BS.1770)", db(d.loudness.integrated_lufs, "LUFS", 2)],
    ["Gain to target", db(d.loudness.gain_to_target_db, "dB", 2)],
    ["Sample peak", db(d.dynamics.peak_dbfs, "dBFS", 2)],
    ["RMS level", db(d.dynamics.rms_dbfs, "dBFS", 2)],
    ["Crest factor", db(d.dynamics.crest_factor_db, "dB", 2)],
    [
      "Clipped samples",
      `${d.clipping.clipped_samples.toLocaleString("en")} (${percent(d.clipping.clipped_ratio)}) in ${d.clipping.clip_events} flat tops`,
    ],
    ["Clip detection level", d.clipping.clip_level ? `${d.clipping.clip_level.toFixed(4)} FS (relative to the file's own peak)` : "Not checked (quiet file)"],
    ["Spectral cliff (decision)", `${d.bandwidth.cutoff_hz.toFixed(0)} Hz of ${d.bandwidth.nyquist_hz.toFixed(0)} Hz Nyquist`],
    ["85% spectral rolloff (reported only)", kHz(d.bandwidth.rolloff_85_hz)],
    ["Flags", `declip ${d.flags.declip ? "on" : "off"}, upsample ${d.flags.upsample ? "on" : "off"}`],
    ["Score", `${d.health_score}/100, grade ${d.grade} (penalties: ${d.issues.map((i) => `${i.code} -${i.penalty}`).join(", ") || "none"})`],
    ["Analysis time", `${d.analysis_seconds.toFixed(2)} s`],
  ];

  const output: [string, string][] = p
    ? [
        ["Outcome", p.outcome],
        ["Upsampling method", p.upsample_method],
        ["Loudness profile", `${p.loudness_profile_label} (target ${p.target_lufs ?? "unchanged"}, ceiling ${p.ceiling_dbtp} dBTP)`],
        ["Output", `${p.output_sample_rate} Hz, ${p.output_bit_depth}-bit PCM WAV`],
        ["Output loudness", db(p.output_lufs, "LUFS", 2)],
        ["Output true peak (16x oversampled)", db(p.output_true_peak_dbtp, "dBTP", 2)],
        ["Limiter gain reduction (max)", db(p.limiter_reduction_db, "dB", 2)],
        ["Output spectral cliff", p.output_metrics ? `${p.output_metrics.cutoff_hz.toFixed(0)} Hz` : "Not measured"],
        ["Device", p.device],
        ["Processing time", `${p.processing_seconds.toFixed(2)} s`],
        ...p.steps.filter((s) => s.detail).map((s): [string, string] => [`Step: ${s.kind}`, s.detail]),
      ]
    : [];

  return (
    <section className={dash.panel} aria-labelledby="expert-title">
      <h2 id="expert-title" className={dash.panelTitle}>
        Expert details
      </h2>
      <div className={styles.expert}>
        <Table title="Input analysis" rows={input} />
        {p && <Table title="Processing" rows={output} />}
      </div>
      <p className={`${dash.small} ${dash.muted}`} style={{ marginTop: 12 }}>
        Job {job.job_id}
        {job.error_code ? `, error ${job.error_code}` : ""}
        {job.error_ref ? `, reference ${job.error_ref}` : ""}
      </p>
    </section>
  );
}

function Table({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div>
      <h3 className={styles.subTitle}>{title}</h3>
      <dl className={styles.table}>
        {rows.map(([label, value]) => (
          <div key={label} className={styles.tableRow}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
