"use client";

// Keeps one job up to date: polls the API until the job finishes, stops on "not found",
// and can fetch fresh signed links on demand (they expire).
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getJob } from "./api";
import { FINISHED, type Job, type JobStatus } from "./types";

const POLL_MS = 1500;
const WARN_AFTER_FAILURES = 3;

export interface JobState {
  job: Job | null;
  /** The last stage the job was seen in before it finished (shows where a failure happened). */
  lastStage: JobStatus | null;
  notFound: boolean;
  connectionError: string | null;
  /** Fetch the job once more (e.g. for fresh audio links). Resolves to the new job or null. */
  refresh: () => Promise<Job | null>;
}

export function useJob(jobId: string, initial: Job | null): JobState {
  const [job, setJob] = useState<Job | null>(initial);
  const [lastStage, setLastStage] = useState<JobStatus | null>(
    initial && !FINISHED.includes(initial.status) ? initial.status : null,
  );
  const [notFound, setNotFound] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [startedFinished] = useState(() => initial !== null && FINISHED.includes(initial.status));
  const [hasInitial] = useState(() => initial !== null);
  const alive = useRef(true);

  const accept = useCallback((latest: Job) => {
    setJob(latest);
    // "queued" again (a retry, or a hand-over to the GPU queue) is not progress: keep the furthest stage
    if (!FINISHED.includes(latest.status) && latest.status !== "queued") setLastStage(latest.status);
  }, []);

  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    async function poll() {
      try {
        const latest = await getJob(jobId);
        if (!alive.current) return;
        failures = 0;
        setConnectionError(null);
        accept(latest);
        if (FINISHED.includes(latest.status)) return; // done: stop asking
      } catch (err) {
        if (!alive.current) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true); // expired or deleted: polling again won't help
          return;
        }
        failures += 1;
        if (failures >= WARN_AFTER_FAILURES) {
          setConnectionError(err instanceof Error ? err.message : "Lost connection to the service");
        }
      }
      // Wait longer after repeated failures, so a down API isn't hammered
      timer = setTimeout(poll, POLL_MS * Math.min(failures + 1, 4));
    }

    if (!startedFinished) timer = setTimeout(poll, hasInitial ? POLL_MS : 0);
    return () => {
      alive.current = false;
      clearTimeout(timer);
    };
  }, [jobId, accept, startedFinished, hasInitial]);

  const refresh = useCallback(async () => {
    try {
      const latest = await getJob(jobId);
      if (alive.current) accept(latest);
      return latest;
    } catch (err) {
      if (alive.current && err instanceof ApiError && err.status === 404) setNotFound(true);
      return null;
    }
  }, [jobId, accept]);

  return { job, lastStage, notFound, connectionError, refresh };
}
