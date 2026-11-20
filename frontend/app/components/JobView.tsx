"use client";

// Everything about one job: the pipeline timeline, the A/B deck, the meter bridge
// (score + findings), before/after numbers and, for engineers, the raw details.
import { useCallback, useEffect, useRef, useState } from "react";

import { deleteJob } from "@/lib/api";
import { useJob } from "@/lib/useJob";
import type { Job } from "@/lib/types";

import ABPlayer, { type FreshLinks } from "./ABPlayer";
import styles from "./Dashboard.module.css";
import ExpertDetails from "./ExpertDetails";
import Findings from "./Findings";
import HealthGauge from "./HealthGauge";
import MetricsStrip from "./MetricsStrip";
import PipelineTimeline from "./PipelineTimeline";
import { useToast } from "./Toasts";

interface Props {
  jobId: string;
  initialJob: Job | null;
  expert: boolean;
  onClose: () => void;
}

const FINISHED_STATUSES = ["completed", "failed"];

export default function JobView({ jobId, initialJob, expert, onClose }: Props) {
  const { job, lastStage, notFound, connectionError, refresh } = useJob(jobId, initialJob);
  const toast = useToast();
  const previousStatus = useRef<string | null>(initialJob?.status ?? null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Tell the person when the job finishes while they wait (not on a page load of a finished job)
  useEffect(() => {
    if (!job) return;
    const previous = previousStatus.current;
    previousStatus.current = job.status;
    if (previous === null || previous === job.status) return;
    if (job.status === "completed") toast("Your track is ready.", "ok");
    if (job.status === "failed") toast("Processing failed. The reason is shown on the page.", "error");
  }, [job, toast]);

  const freshLinks = useCallback(async (): Promise<FreshLinks | null> => {
    const latest = await refresh();
    return latest?.original_url && latest.enhanced_url
      ? { original: latest.original_url, enhanced: latest.enhanced_url, download: latest.download_url }
      : null;
  }, [refresh]);

  async function remove() {
    setDeleting(true);
    try {
      await deleteJob(jobId);
      toast("Deleted the upload and all results.", "ok");
      onClose();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't delete the job.", "error");
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  if (notFound) {
    return (
      <section className={styles.panel} aria-labelledby="gone-title">
        <h2 id="gone-title" className={styles.panelTitle}>
          This job has expired or was deleted
        </h2>
        <p className={styles.panelLead}>Jobs and their files are kept for 7 days. Upload the track again to check it.</p>
        <button type="button" className={styles.button} onClick={onClose}>
          Check another track
        </button>
      </section>
    );
  }

  if (!job) {
    return (
      <section className={styles.panel} aria-busy="true" aria-label="Loading the job">
        <div className={styles.skeleton} style={{ height: 24, width: "40%", marginBottom: 16 }} />
        <div className={styles.skeleton} style={{ height: 56 }} />
      </section>
    );
  }

  const d = job.diagnostics;
  const p = job.processing;
  const finished = FINISHED_STATUSES.includes(job.status);
  const ready = job.status === "completed" && job.original_url !== null && job.enhanced_url !== null;

  return (
    <>
      <section className={styles.panel} aria-labelledby="job-title">
        <h2 id="job-title" className={styles.panelTitle} style={{ overflowWrap: "anywhere" }}>
          {job.original_filename}
        </h2>
        <p className={styles.panelLead} aria-live="polite">
          {job.message ?? "Working on it"}
        </p>
        <PipelineTimeline job={job} lastStage={lastStage} />
        {connectionError && (
          <p className={styles.notice} style={{ marginTop: 16 }} role="status">
            Can&apos;t reach the service right now ({connectionError}). Still trying.
          </p>
        )}
        {job.status === "failed" && (
          <div className={`${styles.notice} ${styles.noticeCritical}`} style={{ marginTop: 16 }} role="alert">
            <p>{job.error ?? "Processing failed."}</p>
            {job.error_ref && <p className={styles.muted}>Reference for support: {job.error_ref}</p>}
            <div className={styles.actions} style={{ marginTop: 10 }}>
              <button type="button" className={styles.button} onClick={onClose}>
                Check another track
              </button>
            </div>
          </div>
        )}
      </section>

      <div className={styles.columns}>
        <section className={styles.panel} aria-labelledby="deck-title">
          <h2 id="deck-title" className={styles.panelTitle}>
            Compare
          </h2>
          {ready ? (
            <ABPlayer
              originalUrl={job.original_url!}
              enhancedUrl={job.enhanced_url!}
              downloadUrl={job.download_url}
              originalLufs={p?.input_lufs ?? d?.loudness.integrated_lufs ?? null}
              enhancedLufs={p?.output_lufs ?? null}
              waveforms={job.waveforms}
              enhancedLabel={p?.outcome === "bypassed" || p?.outcome === "no_signal" ? "Mastered" : "Repaired + mastered"}
              onNeedFreshLinks={freshLinks}
            />
          ) : (
            <div>
              <p className={styles.panelLead}>
                {job.status === "failed"
                  ? "Nothing to compare: processing didn't finish."
                  : "The before-and-after player appears here when your track is ready."}
              </p>
              {job.status !== "failed" && (
                <div aria-hidden="true">
                  <div className={styles.skeleton} style={{ height: 110, marginBottom: 12 }} />
                  <div className={styles.skeleton} style={{ height: 72, marginBottom: 8 }} />
                  <div className={styles.skeleton} style={{ height: 72 }} />
                </div>
              )}
            </div>
          )}
        </section>

        <aside className={styles.panel} aria-labelledby="meter-title">
          <h2 id="meter-title" className={styles.panelTitle}>
            Health check
          </h2>
          {d ? (
            <>
              <HealthGauge diagnostics={d} processing={p} />
              <Findings diagnostics={d} processing={p} />
            </>
          ) : job.status === "failed" ? (
            <p className={styles.panelLead}>The track couldn&apos;t be analysed.</p>
          ) : (
            <div aria-busy="true">
              <span className="visually-hidden">Analysing</span>
              <div className={styles.skeleton} style={{ height: 160, width: 160, borderRadius: "50%", margin: "12px auto" }} />
              <div className={styles.skeleton} style={{ height: 16, marginTop: 16 }} />
              <div className={styles.skeleton} style={{ height: 16, marginTop: 8, width: "70%" }} />
            </div>
          )}
        </aside>
      </div>

      {d && p && <MetricsStrip diagnostics={d} processing={p} />}
      {expert && d && <ExpertDetails job={job} />}

      <section className={styles.actions} aria-label="Manage this job">
        {confirmDelete ? (
          <>
            <span className={styles.small}>Delete the upload and all results now? This can&apos;t be undone.</span>
            <button type="button" className={`${styles.button} ${styles.buttonDanger}`} onClick={() => void remove()} disabled={deleting}>
              {deleting ? "Deleting" : "Delete everything"}
            </button>
            <button type="button" className={styles.button} onClick={() => setConfirmDelete(false)} disabled={deleting}>
              Keep it
            </button>
          </>
        ) : (
          <button
            type="button"
            className={`${styles.button} ${styles.buttonDanger}`}
            onClick={() => setConfirmDelete(true)}
            disabled={!finished}
            title={finished ? undefined : "Available when processing has finished"}
          >
            Delete files now
          </button>
        )}
      </section>
    </>
  );
}
