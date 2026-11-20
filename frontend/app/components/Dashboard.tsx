"use client";

// The whole dashboard. The job being shown lives in the URL (?job=<id>), so a refresh or a
// shared link brings it back.
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { JOB_ID_PATTERN, type Job } from "@/lib/types";
import { usePreference } from "@/lib/usePreference";

import AppHeader from "./AppHeader";
import styles from "./Dashboard.module.css";
import EmptyState from "./EmptyState";
import JobView from "./JobView";
import { ToastProvider } from "./Toasts";
import Uploader from "./Uploader";

export default function Dashboard() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const params = useSearchParams();
  const raw = params.get("job");
  const jobId = raw && JOB_ID_PATTERN.test(raw) ? raw : null;
  const [created, setCreated] = useState<Job | null>(null);
  const [expert, setExpert] = usePreference("agk-expert", "off", ["on", "off"] as const);

  function showJob(job: Job) {
    setCreated(job);
    window.history.replaceState(null, "", `?job=${job.job_id}`);
  }

  function clearJob() {
    window.history.replaceState(null, "", window.location.pathname);
  }

  return (
    <div className={styles.page}>
      <AppHeader expert={expert === "on"} onExpertChange={(on) => setExpert(on ? "on" : "off")} />
      <main className={styles.main}>
        <Uploader onJobCreated={showJob} compact={jobId !== null} />
        {jobId ? (
          <JobView
            key={jobId}
            jobId={jobId}
            initialJob={created?.job_id === jobId ? created : null}
            expert={expert === "on"}
            onClose={clearJob}
          />
        ) : (
          <EmptyState invalidLink={raw !== null} />
        )}
      </main>
      <footer className={styles.footer}>
        Uploads and results are deleted automatically after 7 days. You can delete a job sooner from its page.
      </footer>
    </div>
  );
}
