# Security and privacy

## Data flow and what is stored

| Data | Where | Kept for | Who can read it |
|---|---|---|---|
| The uploaded file | S3/R2 `uploads/<random>/source.<ext>` | 7 days (lifecycle rule), or until `DELETE /jobs/{id}` | Anyone holding a signed link for it (valid 30 min) |
| Mastered WAV and two FLAC playback previews | S3/R2 `outputs/<job_id>/…` | 7 days, or until deleted | Anyone holding a signed link (valid 30 min) |
| The job record: file name, analysis results, status, error code, waveform outline (800 numbers per version) | Redis db 0, key `agk:job:<job_id>` | 7 days (Redis TTL), or until deleted | The API |
| The queue message (job ID only) | Redis db 1 | Until processed | Workers |
| Rate-limit counters (client IP + minute) | Redis `agk:rl:*` | 90 seconds | The API |
| Temporary working files | Worker temp folder | Deleted when the job ends, success or failure | The worker |
| Server logs | Wherever your platform collects stdout | Your log retention | Operators |

No account, email or other personal data is collected. The original file name is stored for
display; it is cleaned of folders, control characters and quotes.

The dashboard states the 7-day retention in its footer and offers "Delete files now" on each job.

## Access model

- **Job IDs and signed links are bearer capabilities.** Job IDs are random 128-bit UUIDs, so
  they can't be guessed, but anyone who has a job's URL can see its results and get fresh
  playback links until the job expires or is deleted. Treat job URLs as private.
- **Signed links are short-lived:** 15 minutes for upload and 30 minutes for playback or download.
- **A signed upload link allows exactly one file** of the declared type and exact byte size:
  `content-length;content-type;host` are signed. The worker re-checks the size and the ETag,
  so an upload replaced after the job was created is refused.
- **The worker only processes keys the API issued.** Keys are validated with a full regex match
  (`uploads/<32 hex>/source.<allowed ext>`), so there is no path traversal and no foreign keys.

## Threats considered and mitigations

| Threat | Mitigation | Status |
|---|---|---|
| Decompression bomb (a small compressed file that decodes to many GB) | Header check before decoding: ≤ 20 min, ≤ 2 channels, ≤ 192 kHz. Container memory limit in compose | Done |
| Poison file that crashes the worker | At most 3 deliveries per job, then failed. Prefork with time limits | Done |
| Storage-cost or GPU abuse | Per-IP rate limit on writes, 200 MB size limit, one job per upload (idempotent) | Basic: no per-tenant quota yet |
| Leaking internals in error messages | Users get fixed public messages and a reference code; details go to server logs only | Done |
| Header injection through file names | RFC 6266 `filename*` encoding; control characters and quotes stripped | Done |
| Signed links leaking through the Referer header | `Referrer-Policy: no-referrer` on the dashboard | Done |
| Clickjacking, content sniffing | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff` | Done |
| Cross-site scripting | React escaping; no raw HTML. A Content Security Policy limits connections and media to the API and S3/R2. Next.js needs `'unsafe-inline'` scripts unless every page is rendered dynamically with nonces | Partial |
| Static cloud keys leaking | `.env` files are git-ignored (history checked clean). Prefer IAM roles: leave the keys empty. Use the least-privilege policy in `infra/iam-app-policy.json` | Done (roles recommended) |
| Bucket made public by mistake | Apply Block Public Access and the TLS-only policy (see below) | Documented: you apply it |
| Plain-text Redis | Use `rediss://` with a password or ACL on a private network; TLS options are preserved by the code | Documented |
| Malicious audio exploiting the decoder (libsndfile) | Workers run as a non-root user in containers, and libsndfile versions are pinned | Partial: no sandbox yet |

## Bucket setup

Run these yourself, with your bucket name. The repository never changes your bucket.

```bash
BUCKET=your-bucket
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-cors --bucket $BUCKET --cors-configuration file://infra/s3-cors.json   # set your real origin
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration file://infra/s3-lifecycle.json
sed "s/YOUR-BUCKET-NAME/$BUCKET/g" infra/s3-bucket-policy.json > /tmp/policy.json && \
  aws s3api put-bucket-policy --bucket $BUCKET --policy file:///tmp/policy.json
aws s3api put-bucket-encryption --bucket $BUCKET --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

In `infra/s3-cors.json`, replace `http://localhost:3000` with your dashboard's real origin. Never use `*`.

## Still missing for production

1. **Real authentication and per-tenant authorization:** OAuth/OIDC or signed platform tokens,
   with job ownership checked on every read. Today job URLs are bearer capabilities.
2. **Per-tenant quotas and abuse monitoring,** beyond the per-IP rate limit.
3. **A sandboxed decoder:** run decoding with no network, a read-only filesystem and seccomp
   (for example gVisor or nsjail), and separate it from the process that holds storage credentials.
4. **Nonce-based CSP:** move to a dynamic render with per-request nonces to drop `'unsafe-inline'`.
5. **Dependency scanning and SBOM in CI:** `pip-audit`, `npm audit` and image scanning.
6. **Audit logging,** and a documented data-processing agreement covering retention and deletion
   for label and artist content.
7. **A confirmed licence for the AudioSR model weights** before commercial use.
