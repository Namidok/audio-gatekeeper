// What the pipeline timeline shows, as a pure function (unit-tested in tests/timeline.test.mjs).
//
// A job can go back to "queued" for two reasons, and the worker then STARTS OVER from Fetch:
//   - a retry after a temporary problem   -> say so ("attempt 2 of 3"), nothing is shown as done
//   - a hand-over to the GPU (AI) worker  -> analysis is known, repair is what it's waiting for
import type { Job, JobStatus } from "./types";

export type NodeState = "done" | "current" | "pending" | "skipped" | "partial" | "failed";

export interface TimelineNode {
  status: JobStatus;
  label: string;
  state: NodeState;
  caption: string;
}

export interface TimelineView {
  note: string | null; // one line above the timeline, or null
  nodes: TimelineNode[];
}

export const STAGES: { status: JobStatus; label: string }[] = [
  { status: "downloading", label: "Fetch" },
  { status: "diagnosing", label: "Analyse" },
  { status: "enhancing", label: "Repair" },
  { status: "mastering", label: "Master" },
  { status: "uploading", label: "Save" },
  { status: "completed", label: "Ready" },
];
const REPAIR = 2;

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

export function timelineView(job: Job, lastStage: JobStatus | null): TimelineView {
  const failed = job.status === "failed";
  const finished = failed || job.status === "completed";
  const d = job.diagnostics;
  const repairNeeded = d ? d.flags.declip || d.flags.upsample : null;
  const onGpu = job.queue === "gpu";
  const waitingForGpu = job.status === "queued" && onGpu && d !== null;
  const attemptText = `attempt ${job.attempts} of ${job.max_attempts}`;

  // Where the job is now
  let current: number;
  if (failed) current = Math.max(0, stageIndex(lastStage));
  else if (waitingForGpu) current = REPAIR;
  else current = stageIndex(job.status); // "queued" (first wait or a retry) is -1: nothing started

  // The one-line note above the timeline
  let note: string | null = null;
  if (job.status === "queued") {
    if (waitingForGpu) note = "Analysis done. Waiting for a GPU worker to restore the high frequencies.";
    else if (job.attempts > 0)
      note = `Retrying after a temporary problem: attempt ${job.attempts + 1} of ${job.max_attempts}. It starts again from the beginning.`;
    else note = job.message ?? "Waiting for a worker";
  } else if (!finished && onGpu) {
    note = `On the GPU worker${job.attempts > 1 ? ` (${attemptText})` : ""}: it fetches and checks the file again, then restores the high frequencies.`;
  } else if (!finished && job.attempts > 1) {
    note = `Retrying: ${attemptText}.`;
  }

  const nodes = STAGES.map((stage, i): TimelineNode => {
    let state: NodeState;
    if (i === REPAIR && repairNeeded === false && current > REPAIR) state = "skipped";
    else if (i === REPAIR && job.processing?.outcome === "partial") state = "partial";
    else if (failed) state = i < current ? "done" : i === current ? "failed" : "pending";
    else if (job.status === "completed" || i < current) state = "done";
    else state = i === current ? "current" : "pending";

    let caption = CAPTION[state];
    if (state === "skipped") caption = d?.has_signal === false ? "Skipped: silent track" : "Skipped: no damage";
    if (state === "partial") caption = "Highs not restored";
    if (state === "current" && waitingForGpu) caption = "Waiting for GPU";
    return { status: stage.status, label: stage.label, state, caption };
  });

  return { note, nodes };
}
