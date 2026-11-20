// Plain-language findings and what the pipeline did about them.
import type { Diagnostics, Processing, Severity, Step } from "@/lib/types";

import styles from "./Report.module.css";

const ORDER: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };
const SEVERITY_TEXT: Record<Severity, string> = { critical: "Serious", warning: "Worth fixing", info: "Note" };
const STEP_ICON: Record<Step["status"], string> = { applied: "✓", skipped: "–", fallback: "!" };
const STEP_TEXT: Record<Step["status"], string> = { applied: "Done", skipped: "Skipped", fallback: "Only partly done" };

export default function Findings({ diagnostics: d, processing: p }: { diagnostics: Diagnostics; processing: Processing | null }) {
  const issues = [...d.issues].sort((a, b) => ORDER[a.severity] - ORDER[b.severity]);

  return (
    <div className={styles.findings}>
      <h3 className={styles.subTitle}>What we found</h3>
      {issues.length === 0 ? (
        <p className={styles.ok}>No problems found.</p>
      ) : (
        <ul className={styles.issues}>
          {issues.map((issue) => (
            <li key={issue.code} className={`${styles.issue} ${styles[issue.severity]}`}>
              <span className={styles.badge}>{SEVERITY_TEXT[issue.severity]}</span>
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      )}

      {p && (
        <>
          <h3 className={styles.subTitle}>What we did</h3>
          <ul className={styles.steps}>
            {p.steps.map((step) => (
              <li key={step.kind} className={`${styles.step} ${styles[step.status]}`}>
                <span className={styles.stepIcon} aria-hidden="true">
                  {STEP_ICON[step.status]}
                </span>
                <span>
                  <span className="visually-hidden">{STEP_TEXT[step.status]}: </span>
                  {step.summary}
                </span>
              </li>
            ))}
          </ul>
          {p.notes.map((note) => (
            <p key={note} className={styles.note}>
              {note}
            </p>
          ))}
        </>
      )}
    </div>
  );
}
