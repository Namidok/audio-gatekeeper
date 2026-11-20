# API

Base URL: `http://localhost:8000` in development. JSON in, JSON out. The full OpenAPI schema is at
`/openapi.json` and the interactive docs at `/docs` (both switch off with `ENABLE_DOCS=false`).

**Write requests** (`POST`, `DELETE`) are rate-limited per client IP (`RATE_LIMIT_PER_MINUTE`, default 30).
When `API_KEY` is set, they also need the header `X-API-Key: <key>`.

Errors always look like `{"detail": "plain-language message"}`. Validation errors (422) are
FastAPI's standard `{"detail": [{"loc": [...], "msg": "..."}]}`.

## `GET /health`

Is the API up, and can it reach its dependencies? `?full=true` also checks S3 access (one HEAD request).

```json
{ "status": "ok", "redis": true, "queue": true, "storage": null }
```

`status` is `"degraded"` if any check fails. `storage` is `null` unless `full=true` is set.

## `GET /profiles`

The loudness profiles a job can be mastered for, and where their numbers come from.

```json
[
  { "name": "spotify", "label": "Spotify (-14 LUFS)", "target_lufs": -14.0, "ceiling_dbtp": -1.0,
    "source": "Spotify for Artists: Loudness normalization" },
  { "name": "youtube", "label": "YouTube (-14 LUFS, unofficial)", "target_lufs": -14.0, "ceiling_dbtp": -1.0,
    "source": "Third-party measurements; YouTube publishes no official target" },
  { "name": "preserve", "label": "Keep loudness, fix peaks only", "target_lufs": null, "ceiling_dbtp": -1.0,
    "source": "Peak safety only, per Spotify's true-peak advice" },
  { "name": "custom", "label": "Custom (-14 LUFS)", "target_lufs": -14.0, "ceiling_dbtp": -1.0,
    "source": "Operator configuration" }
]
```

## `POST /uploads`

Step 1: get a signed link to upload one file straight to storage.

```json
// request
{ "filename": "my_song.mp3", "size_bytes": 8400000 }
// 200 response
{
  "object_key": "uploads/3f2c…e91a/source.mp3",
  "upload_url": "https://bucket.s3.eu-central-1.amazonaws.com/uploads/…?X-Amz-Signature=…",
  "method": "PUT",
  "headers": { "Content-Type": "audio/mpeg" },
  "expires_in": 900
}
```

Then `PUT` the file bytes to `upload_url` with exactly those headers. The content type and the
exact size are part of the signature, so a different file size is refused by storage.

| Code | When |
|---|---|
| 413 | `size_bytes` is over `MAX_UPLOAD_BYTES` (200 MB) |
| 415 | Extension is not one of wav, flac, mp3, ogg, aif, aiff |
| 422 | Missing or invalid fields (for example, size 0, empty filename) |
| 401 / 429 | API key missing or wrong / rate limit exceeded (with `Retry-After`) |

## `POST /jobs`

Step 2: the upload finished, so queue it. **Idempotent per upload:** sending the same
`object_key` again returns the existing job and queues nothing new.

```json
// request
{ "object_key": "uploads/3f2c…e91a/source.mp3", "filename": "my_song.mp3", "loudness_profile": "spotify" }
// 202 response: a Job (see below) with "status": "queued"
```

| Code | When |
|---|---|
| 400 | `object_key` is not one this API issued, or the file isn't in storage yet |
| 413 | The stored file is over the size limit |
| 502 | Storage is not reachable |
| 503 | The processing queue or the job database is unavailable. The job is not created, and you can retry with the same `object_key` |

## `GET /jobs/{job_id}`

Step 3: poll for progress and results. Signed links are fresh on every call and expire after
`links_expire_in` seconds (default 1800).

```json
{
  "job_id": "bbf50a41fe7c47d68cb6d4932e121502",
  "status": "completed",
  "progress": 100,
  "message": "Done, but not every repair was possible (see the notes)",
  "error": null, "error_code": null, "error_ref": null,
  "original_filename": "smoke_test_synthetic.wav",
  "loudness_profile": "spotify",
  "diagnostics": {
    "health_score": 55, "grade": "D", "has_signal": true,
    "flags": { "upsample": true, "declip": true },
    "sample_rate": 44100, "channels": 2, "duration_seconds": 8.0,
    "clipping": { "clipped_samples": 319, "clipped_ratio": 0.00045, "clip_events": 46, "clip_level": 0.99, "is_clipped": true },
    "dynamics": { "peak_dbfs": 0.0, "rms_dbfs": -12.82, "crest_factor_db": 12.82, "is_squashed": false },
    "bandwidth": { "rolloff_85_hz": 7900.0, "cutoff_hz": 11000.0, "nyquist_hz": 22050.0, "is_band_limited": true, "limited_by_sample_rate": false },
    "loudness": { "integrated_lufs": -11.69, "gain_to_target_db": -2.31 },
    "issues": [
      { "code": "clipping", "severity": "warning", "message": "Light clipping: a few peaks are flattened (0.045% of samples)", "penalty": 15 },
      { "code": "band_limited", "severity": "warning", "message": "High frequencies are cut off at 11.0 kHz, typical of a low-bitrate MP3", "penalty": 30 }
    ],
    "analysis_seconds": 0.68
  },
  "processing": {
    "outcome": "partial", "upsample_method": "resample_only", "highs_restored": false, "ai_bypassed": false,
    "steps": [
      { "kind": "declip", "status": "applied", "summary": "Rebuilt the flattened peaks", "detail": "declip: 46 flat tops repaired (cubic spline)" },
      { "kind": "bandwidth_extension", "status": "fallback", "summary": "Converted to 48 kHz without restoring the missing highs", "detail": "resample_only 44100 -> 48000 Hz (torchaudio sinc/Kaiser)" },
      { "kind": "master", "status": "applied", "summary": "Mastered for Spotify (-14 LUFS): set loudness to -14 LUFS, peaks at or below -1 dBTP", "detail": "gain -2.3 dB, limiter 4.2 dB, true peak -1.20 dBTP (16x oversampled)" }
    ],
    "notes": ["High frequencies were not restored: the AI model is not installed on this server."],
    "loudness_profile": "spotify", "target_lufs": -14.0, "ceiling_dbtp": -1.0,
    "input_lufs": -11.69, "output_lufs": -14.04, "input_true_peak_dbtp": 0.34, "output_true_peak_dbtp": -1.2,
    "output_metrics": { "clipped_ratio": 0.0, "cutoff_hz": 11000.0, "nyquist_hz": 24000.0 },
    "output_sample_rate": 48000, "output_bit_depth": 24, "limiter_reduction_db": 4.23,
    "device": "mps (Apple Silicon GPU)", "processing_seconds": 0.78
  },
  "original_url": "https://…/outputs/<job>/original_preview.flac?…",
  "enhanced_url": "https://…/outputs/<job>/enhanced_preview.flac?…",
  "download_url": "https://…/outputs/<job>/master.wav?…response-content-disposition=attachment…",
  "links_expire_in": 1800,
  "queue": "default", "attempts": 1, "max_attempts": 3,
  "waveforms": { "original": [0.41, 0.52, "… 800 values"], "enhanced": ["… 800 values"] },
  "created_at": "2026-11-19T00:53:02.118421+00:00",
  "updated_at": "2026-11-19T00:53:04.903117+00:00"
}
```

`outcome` is one of:
- `bypassed`: no damage found; only loudness and peaks were adjusted.
- `repaired`: every flagged repair was done.
- `partial`: a flagged repair wasn't possible; see `notes`.
- `no_signal`: the track is silent or too short; nothing was changed.

`status` values: `queued`, `downloading`, `diagnosing`, `enhancing`, `mastering`, `uploading`,
`completed`, `failed`.

A job can return to `queued` after it started, and the worker then starts over from
`downloading`. There are two reasons:
- **A retry after a temporary problem:** `queue` stays `"default"` and `attempts` shows how many
  starts have happened, up to `max_attempts`.
- **A hand-over to the GPU worker for AI repair:** `queue` becomes `"gpu"`, and `attempts` is reset
  for the GPU worker's own tries. The hand-over doesn't count as a failed attempt.

| Code | When |
|---|---|
| 404 | Unknown, deleted, or expired job (jobs are kept for 7 days) |
| 503 | The job database is unavailable |

### Job error codes (`error_code` on failed jobs)

| Code | Meaning | Retry helps? |
|---|---|---|
| `unreadable`, `undecodable`, `empty`, `invalid_samples` | The file isn't valid audio | No |
| `too_long`, `too_many_channels`, `sample_rate_too_high`, `too_large` | Over a limit | No |
| `upload_missing`, `upload_changed`, `invalid_reference` | The upload isn't there, or was replaced after the job was created | Upload again |
| `timeout` | Processing hit the 28-minute limit | No |
| `too_many_attempts` | The file crashed the worker repeatedly | No |
| `stalled` | The worker stopped responding | Yes |
| `queue_unavailable` | The queue was down when the job was created | Yes |
| `storage_error`, `internal_error` | Server-side failure. `error_ref` points to the server log entry | Maybe |

## `DELETE /jobs/{job_id}`

Deletes the upload, the master, the previews and the job record. Returns **204**, **404** if
the job doesn't exist, or **409** if it is still processing (a running worker would otherwise
write its results back after the delete). Rate-limited, and needs the API key when one is configured.
