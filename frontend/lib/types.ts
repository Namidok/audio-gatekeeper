// Step 22: the shapes of the data the API sends back (mirrors backend/app/main.py).

export type JobStatus =
  | "queued"
  | "downloading"
  | "diagnosing"
  | "enhancing"
  | "mastering"
  | "uploading"
  | "completed"
  | "failed";

export interface UploadResponse {
  object_key: string;
  upload_url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_in: number;
}

export interface Issue {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  penalty: number;
}

export interface Diagnostics {
  health_score: number;
  grade: string;
  flags: { upsample: boolean; declip: boolean };
  sample_rate: number;
  channels: number;
  duration_seconds: number;
  clipping: { clipped_samples: number; clipped_ratio: number; clip_events: number; is_clipped: boolean };
  dynamics: { peak_dbfs: number; rms_dbfs: number; crest_factor_db: number; is_squashed: boolean };
  bandwidth: { rolloff_85_hz: number; cutoff_hz: number; nyquist_hz: number; is_band_limited: boolean };
  loudness: { integrated_lufs: number | null; gain_to_target_db: number | null };
  issues: Issue[];
  analysis_seconds: number;
}

export interface Processing {
  device: string;
  ai_bypassed: boolean;
  steps_applied: string[];
  steps_skipped: string[];
  notes: string[];
  output_sample_rate: number;
  output_bit_depth: number;
  input_lufs: number | null;
  output_lufs: number | null;
  output_true_peak_dbtp: number | null;
  limiter_reduction_db: number;
  processing_seconds: number;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  progress: number;
  message: string | null;
  error: string | null;
  original_filename: string;
  diagnostics: Diagnostics | null;
  processing: Processing | null;
  original_url: string | null;
  enhanced_url: string | null;
  created_at: string;
  updated_at: string;
}

export const FINISHED: JobStatus[] = ["completed", "failed"];
