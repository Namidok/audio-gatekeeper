// Every call the dashboard makes to the backend, in one place.
import type { Health, Job, Profile, ProfileName, UploadResponse } from "./types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** An error from our API, with the HTTP status so callers can tell "expired" from "offline". */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI errors are {"detail": "..."} or, for validation, {"detail": [{"msg": "..."}]}. */
function readDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === "string") return detail[0].msg;
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...init,
      // Only send a JSON content type when there is a body: a GET with it needs a CORS preflight
      headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
    });
  } catch {
    throw new ApiError("The service can't be reached. Check your connection and try again.", 0);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    throw new ApiError(readDetail(body, `${response.status} ${response.statusText}`.trim()), response.status);
  }
  if (body === null) throw new ApiError("The service sent an unreadable answer.", response.status);
  return body as T;
}

export function checkHealth(full = false): Promise<Health> {
  return request(`/health${full ? "?full=true" : ""}`);
}

export function getProfiles(): Promise<Profile[]> {
  return request("/profiles");
}

/** 1. Ask the API for a signed S3 upload link. */
export function requestUpload(file: File): Promise<UploadResponse> {
  return request("/uploads", {
    method: "POST",
    body: JSON.stringify({ filename: file.name, size_bytes: file.size }),
  });
}

const S3_MESSAGES: Record<string, string> = {
  AccessDenied: "The upload link was refused. It may have expired: please try again.",
  SignatureDoesNotMatch: "The upload link was refused. Please try again.",
  RequestTimeTooSkewed: "Your computer's clock is wrong, so the upload was refused. Fix the clock and try again.",
  EntityTooLarge: "The file is too large.",
};
const STALL_MS = 60_000; // give up if the upload makes no progress for a minute

/** 2. Send the file straight to S3. Uses XMLHttpRequest because fetch can't report upload progress. */
export function uploadToS3(
  file: File,
  upload: UploadResponse,
  onProgress: (percent: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
    let stalled = false;
    const onAbort = () => xhr.abort();
    const armStallTimer = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => {
        stalled = true;
        xhr.abort();
      }, STALL_MS);
    };
    const cleanUp = () => {
      clearTimeout(stallTimer);
      signal?.removeEventListener("abort", onAbort);
    };

    xhr.open(upload.method, upload.upload_url);
    for (const [name, value] of Object.entries(upload.headers)) {
      xhr.setRequestHeader(name, value); // must match what was signed
    }
    xhr.upload.onprogress = (event) => {
      armStallTimer();
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      cleanUp();
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      const code = /<Code>([^<]+)<\/Code>/.exec(xhr.responseText ?? "")?.[1] ?? "";
      reject(new ApiError(S3_MESSAGES[code] ?? `The upload failed (storage answered ${xhr.status}).`, xhr.status));
    };
    xhr.onerror = () => {
      cleanUp();
      reject(new ApiError("The upload failed: network problem, or the storage bucket's CORS rule is missing.", 0));
    };
    xhr.onabort = () => {
      cleanUp();
      reject(
        stalled
          ? new ApiError("The upload stopped making progress, so it was cancelled. Please try again.", 0)
          : new DOMException("Upload cancelled", "AbortError"),
      );
    };

    if (signal?.aborted) return reject(new DOMException("Upload cancelled", "AbortError"));
    signal?.addEventListener("abort", onAbort);
    armStallTimer();
    xhr.send(file);
  });
}

/** 3. Tell the API the upload is done, so it queues the job. */
export function createJob(objectKey: string, filename: string, profile?: ProfileName): Promise<Job> {
  return request("/jobs", {
    method: "POST",
    body: JSON.stringify({ object_key: objectKey, filename, loudness_profile: profile ?? null }),
  });
}

/** 4. Ask how the job is doing. Signed links in the answer are fresh every time. */
export function getJob(jobId: string): Promise<Job> {
  return request(`/jobs/${jobId}`);
}

/** Delete a job and every file it created. */
export function deleteJob(jobId: string): Promise<void> {
  return request(`/jobs/${jobId}`, { method: "DELETE" });
}
