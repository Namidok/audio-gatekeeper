// The shapes of the data the API sends back. Mirrors backend/app/schemas.py: change both together.

export type JobStatus =
  | "queued"
  | "downloading"
  | "diagnosing"
  | "enhancing"
  | "mastering"
  | "uploading"
  | "completed"
  | "failed";

export type Severity = "info" | "warning" | "critical";
export type Grade = "A" | "B" | "C" | "D" | "F";
export type Outcome = "bypassed" | "repaired" | "partial" | "no_signal";
export type UpsampleMethod = "audiosr" | "resample_only" | "none";
export type ProfileName = "spotify" | "youtube" | "preserve" | "custom";

export interface UploadResponse {
  object_key: string;
  upload_url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_in: number;
}

export interface Issue {
  code: string;
  severity: Severity;
  message: string;
  penalty: number;
}

export interface Diagnostics {
  health_score: number;
  grade: Grade;
  flags: { upsample: boolean; declip: boolean };
  sample_rate: number;
  channels: number;
  duration_seconds: number;
  clipping: {
    clipped_samples: number;
    clipped_ratio: number;
    clip_events: number;
    clip_level: number;
    is_clipped: boolean;
  };
  dynamics: { peak_dbfs: number; rms_dbfs: number; crest_factor_db: number; is_squashed: boolean };
  bandwidth: {
    rolloff_85_hz: number;
    cutoff_hz: number;
    nyquist_hz: number;
    is_band_limited: boolean;
    limited_by_sample_rate: boolean;
  };
  loudness: { integrated_lufs: number | null; gain_to_target_db: number | null };
  has_signal: boolean;
  issues: Issue[];
  analysis_seconds: number;
}

export interface Step {
  kind: "declip" | "bandwidth_extension" | "resample" | "master";
  status: "applied" | "skipped" | "fallback";
  summary: string; // plain language
  detail: string; // technical, for expert details
}

export interface Processing {
  device: string;
  ai_bypassed: boolean;
  outcome: Outcome;
  upsample_method: UpsampleMethod;
  highs_restored: boolean;
  steps: Step[];
  steps_applied: string[];
  steps_skipped: string[];
  notes: string[];
  loudness_profile: string;
  loudness_profile_label: string;
  target_lufs: number | null;
  ceiling_dbtp: number;
  output_sample_rate: number;
  output_bit_depth: number;
  input_lufs: number | null; // the original upload, untouched
  output_lufs: number | null;
  input_true_peak_dbtp: number | null;
  output_true_peak_dbtp: number | null;
  limiter_reduction_db: number;
  output_metrics: { clipped_ratio: number; cutoff_hz: number; nyquist_hz: number } | null;
  processing_seconds: number;
}

export interface Waveforms {
  original: number[];
  enhanced: number[];
}

export interface Job {
  job_id: string;
  status: JobStatus;
  progress: number;
  message: string | null;
  error: string | null; // safe to show
  error_code: string | null;
  error_ref: string | null; // quote this when reporting a problem
  original_filename: string;
  loudness_profile: string;
  diagnostics: Diagnostics | null;
  processing: Processing | null;
  original_url: string | null;
  enhanced_url: string | null;
  download_url: string | null;
  links_expire_in: number | null;
  queue: "default" | "gpu"; // "gpu": handed to the AI worker for high-frequency repair
  attempts: number; // worker starts so far (on the current queue)
  max_attempts: number;
  waveforms: Waveforms | null;
  created_at: string;
  updated_at: string;
}

export interface Health {
  status: "ok" | "degraded";
  redis: boolean;
  queue: boolean;
  storage: boolean | null;
}

export interface Profile {
  name: ProfileName;
  label: string;
  target_lufs: number | null;
  ceiling_dbtp: number;
  source: string;
}

export const FINISHED: JobStatus[] = ["completed", "failed"];
export const JOB_ID_PATTERN = /^[0-9a-f]{32}$/;
