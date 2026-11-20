// The pipeline as a connected timeline. The running stage pulses; skipped repair is shown
// honestly as skipped (the gatekeeper bypass), not as done. The rules live in lib/timeline.ts.
import { timelineView } from "@/lib/timeline";
import type { Job, JobStatus } from "@/lib/types";

import styles from "./PipelineTimeline.module.css";

export default function PipelineTimeline({ job, lastStage }: { job: Job; lastStage: JobStatus | null }) {
  const { note, nodes } = timelineView(job, lastStage);

  return (
    <div className={styles.wrap}>
      {note && <p className={styles.queued}>{note}</p>}
      <ol className={styles.timeline} aria-label="Processing steps">
        {nodes.map((node) => (
          <li
            key={node.status}
            className={`${styles.node} ${styles[node.state]}`}
            aria-current={node.state === "current" ? "step" : undefined}
          >
            <span className={styles.dot} aria-hidden="true">
              {node.state === "done" ? "✓" : node.state === "failed" ? "!" : node.state === "skipped" ? "–" : ""}
            </span>
            <span className={styles.label}>{node.label}</span>
            <span className={styles.caption}>
              {node.caption || <span className="visually-hidden">Not started</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
