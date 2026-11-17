// Step 22: every call the dashboard makes to the backend, in one place.
import type { Job, UploadResponse } from "./types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Turns an error response from FastAPI ({"detail": "..."}) into a readable Error. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new Error(`Cannot reach the API at ${API}. Is uvicorn running?`);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body as T;
}

export function checkHealth(): Promise<{ status: string; redis: boolean }> {
  return request("/health");
}

/** 1. Ask the API for a signed S3 upload link. */
export function requestUpload(file: File): Promise<UploadResponse> {
  return request("/uploads", {
    method: "POST",
    body: JSON.stringify({ filename: file.name, size_bytes: file.size }),
  });
}

/** 2. Send the file straight to S3. Uses XMLHttpRequest because fetch can't report upload progress. */
export function uploadToS3(
  file: File,
  upload: UploadResponse,
  onProgress: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(upload.method, upload.upload_url);
    for (const [name, value] of Object.entries(upload.headers)) {
      xhr.setRequestHeader(name, value);              // must match what was signed
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`Upload to storage failed (HTTP ${xhr.status})`));
    xhr.onerror = () => reject(new Error("Upload to storage failed: network or CORS error"));
    xhr.send(file);
  });
}

/** 3. Tell the API the upload is done, so it queues the job. */
export function createJob(objectKey: string, filename: string): Promise<Job> {
  return request("/jobs", {
    method: "POST",
    body: JSON.stringify({ object_key: objectKey, filename }),
  });
}

/** 4. Ask how the job is doing (the dashboard calls this every 1.5 seconds). */
export function getJob(jobId: string): Promise<Job> {
  return request(`/jobs/${jobId}`);
}
