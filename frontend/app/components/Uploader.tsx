"use client";

// "Signal in": drag-and-drop uploader. File -> signed link -> straight to S3 -> job created.
import { useEffect, useRef, useState } from "react";

import { ApiError, createJob, getProfiles, requestUpload, uploadToS3 } from "@/lib/api";
import type { Job, Profile, ProfileName } from "@/lib/types";

import { useToast } from "./Toasts";
import styles from "./Uploader.module.css";

// Must match the backend (storage.ALLOWED_TYPES and MAX_UPLOAD_BYTES); the backend re-checks both.
const ALLOWED = ["wav", "flac", "mp3", "ogg", "aif", "aiff"] as const;
const MAX_BYTES = 200 * 1024 * 1024;
const ACCEPT = `${ALLOWED.map((ext) => `.${ext}`).join(",")},audio/*`;
const HINT = "WAV, FLAC, MP3, OGG or AIFF, up to 200 MB and 20 minutes";
const BARS = 48;

const FALLBACK_PROFILES: Pick<Profile, "name" | "label">[] = [
  { name: "spotify", label: "Spotify (-14 LUFS)" },
  { name: "youtube", label: "YouTube (-14 LUFS, unofficial)" },
  { name: "preserve", label: "Keep loudness, fix peaks only" },
];

type Phase = "idle" | "preparing" | "uploading" | "queuing";

interface Props {
  onJobCreated: (job: Job) => void;
  compact: boolean;
}

function problemWith(file: File): string | null {
  const ext = file.name.includes(".") ? (file.name.split(".").pop() ?? "").toLowerCase() : "";
  if (!(ALLOWED as readonly string[]).includes(ext)) {
    return ext
      ? `“.${ext}” files aren't supported. Use WAV, FLAC, MP3, OGG or AIFF.`
      : "The file has no extension, so its format is unknown.";
  }
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_BYTES) return `The file is ${(file.size / 1_048_576).toFixed(0)} MB; the limit is 200 MB.`;
  return null;
}

export default function Uploader({ onJobCreated, compact }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const toast = useToast();
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [percent, setPercent] = useState(0);
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lastFile, setLastFile] = useState<File | null>(null);
  const [profiles, setProfiles] = useState<Pick<Profile, "name" | "label">[]>(FALLBACK_PROFILES);
  const [profile, setProfile] = useState<ProfileName>("spotify");
  const busy = phase !== "idle";

  useEffect(() => {
    let alive = true;
    getProfiles()
      .then((list) => {
        if (alive && list.length) setProfiles(list.filter((p) => p.name !== "custom"));
      })
      .catch(() => {}); // the built-in list is fine
    return () => {
      alive = false;
    };
  }, []);

  // A file dropped *outside* the box would make the browser open it and leave the app
  useEffect(() => {
    const block = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => {
      window.removeEventListener("dragover", block);
      window.removeEventListener("drop", block);
    };
  }, []);

  async function handleFile(file: File) {
    const problem = problemWith(file);
    if (problem) {
      setError(problem);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setLastFile(file);
    setFileName(file.name);
    setPercent(0);
    try {
      setPhase("preparing");
      const upload = await requestUpload(file); // 1. signed link from our API

      setPhase("uploading");
      await uploadToS3(file, upload, setPercent, controller.signal); // 2. straight to S3

      setPhase("queuing");
      const job = await createJob(upload.object_key, file.name, profile); // 3. start processing
      setLastFile(null);
      toast("Uploaded. Checking your track now.", "ok");
      onJobCreated(job);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        toast("Upload cancelled.");
      } else {
        setError(err instanceof Error ? err.message : "The upload failed. Please try again.");
        if (err instanceof ApiError && err.status === 413) setLastFile(null); // retrying won't help
      }
    } finally {
      abortRef.current = null;
      setPhase("idle");
      if (inputRef.current) inputRef.current.value = ""; // allow picking the same file again
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file && !busy) void handleFile(file);
  }

  function onDragLeave(e: React.DragEvent) {
    // Moving over a child element also fires "leave": only react when really leaving the box
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
  }

  function openPicker() {
    if (!busy) inputRef.current?.click();
  }

  const title = {
    idle: dragging ? "Drop it to check it" : "Drop a track to check it, or choose a file",
    preparing: `Preparing ${fileName}`,
    uploading: `Uploading ${fileName}`,
    queuing: "Starting the analysis",
  }[phase];

  return (
    <section className={`${styles.wrap} ${compact ? styles.compact : ""}`} aria-label="Upload a track">
      <div
        role="button"
        tabIndex={0}
        aria-disabled={busy}
        aria-describedby="upload-hint"
        className={`${styles.zone} ${dragging ? styles.dragging : ""} ${busy ? styles.busy : ""}`}
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault(); // Space would otherwise scroll the page
            openPicker();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <div className={styles.line} aria-hidden="true">
          {Array.from({ length: BARS }, (_, i) => (
            <span key={i} style={{ "--i": i } as React.CSSProperties} />
          ))}
        </div>
        <p className={styles.title}>{title}</p>
        <p id="upload-hint" className={styles.hint}>
          {HINT}
        </p>
        {phase === "uploading" && (
          <div
            className={styles.bar}
            role="progressbar"
            aria-label="Upload progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <div className={styles.fill} style={{ width: `${percent}%` }} />
          </div>
        )}
        <input
          ref={inputRef}
          type="file"
          name="audio"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
      </div>

      <div className={styles.row}>
        <label className={styles.profile}>
          <span>Master for</span>
          <select name="loudness_profile" value={profile} onChange={(e) => setProfile(e.target.value as ProfileName)} disabled={busy}>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {phase === "uploading" && (
          <>
            <span className={styles.percent} aria-hidden="true">
              {percent}%
            </span>
            <button type="button" className={styles.action} onClick={() => abortRef.current?.abort()}>
              Cancel upload
            </button>
          </>
        )}
      </div>

      {error && (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          {lastFile && !busy && (
            <button type="button" className={styles.action} onClick={() => void handleFile(lastFile)}>
              Try again
            </button>
          )}
        </div>
      )}
    </section>
  );
}
