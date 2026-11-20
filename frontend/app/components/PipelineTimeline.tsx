// The pipeline as a connected timeline. The running stage pulses; skipped repair is shown
// honestly as skipped (the gatekeeper bypass), not as done.
import type { Job, JobStatus } from "@/lib/types";

import styles from "./PipelineTimeline.module.css";

const STAGES: { status: JobStatus; label: string }[] = [
  { status: "downloading", label: "Fetch" },
  { status: "diagnosing", label: "Analyse" },
  { status: "enhancing", label: "Repair" },
  { status: "mastering", label: "Master" },
  { status: "uploading", label: "Save" },
  { status: "completed", label: "Ready" },
];
const REPAIR = 2;

type NodeState = "done" | "current" | "pending" | "skipped" | "partial" | "failed";
const CAPTION: Record<NodeState, string> = {
  done: "Done",
  current: "In progress",
  pending: "",
  skipped: "Skipped",
  partial: "Partly done",
  failed: "Stopped here",
};

function stageIndex(status: JobStatus | null): number {
  return status ? STAGES.findIndex((s) => s.status === status) : -1;
}

export default function PipelineTimeline({ job, lastStage }: { job: Job; lastStage: JobStatus | null }) {
  const failed = job.status === "failed";
  const current = failed ? Math.max(0, stageIndex(lastStage)) : stageIndex(job.status);
  const d = job.diagnostics;
  const repairNeeded = d ? d.flags.declip || d.flags.upsample : null;

  function stateOf(i: number): NodeState {
    if (i === REPAIR && repairNeeded === false && current > REPAIR) return "skipped";
    if (i === REPAIR && job.processing?.outcome === "partial") return "partial";
    if (failed) return i < current ? "done" : i === current ? "failed" : "pending";
    if (job.status === "completed" || i < current) return "done";
    return i === current ? "current" : "pending";
  }

  function caption(state: NodeState): string {
    if (state === "skipped") return d?.has_signal === false ? "Skipped: silent track" : "Skipped: no damage";
    if (state === "partial") return "Highs not restored";
    return CAPTION[state];
  }

  return (
    <div className={styles.wrap}>
      {job.status === "queued" && <p className={styles.queued}>{job.message ?? "Waiting for a worker"}</p>}
      <ol className={styles.timeline} aria-label="Processing steps">
        {STAGES.map((stage, i) => {
          const state = stateOf(i);
          const text = caption(state);
          return (
            <li key={stage.status} className={`${styles.node} ${styles[state]}`} aria-current={state === "current" ? "step" : undefined}>
              <span className={styles.dot} aria-hidden="true">
                {state === "done" ? "✓" : state === "failed" ? "!" : state === "skipped" ? "–" : ""}
              </span>
              <span className={styles.label}>{stage.label}</span>
              <span className={styles.caption}>{text || <span className="visually-hidden">Not started</span>}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
