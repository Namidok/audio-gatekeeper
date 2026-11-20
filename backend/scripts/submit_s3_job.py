"""Upload a file to S3 the way the browser will, run it through the worker, get playback links.

Usage: python submit_s3_job.py some_file.mp3
"""
import sys
import time
import urllib.request
from pathlib import Path

from app.job_store import JobStore
from app.storage import Storage, content_type_for, new_upload_key
from app.worker import process_job

path = Path(sys.argv[1])
storage, store = Storage(), JobStore()

# 1. Upload straight to S3 with a signed link (the browser's job in the real app)
_, content_type = content_type_for(path.name)
key = new_upload_key(path.name)
data = path.read_bytes()
url = storage.presign_upload(key, content_type, len(data))
urllib.request.urlopen(urllib.request.Request(url, data=data, method="PUT",
                                              headers={"Content-Type": content_type}))
print(f"Uploaded {path.name} -> s3://{storage.bucket}/{key}")

# 2. Create the job and hand it to the worker (the API's job in the real app)
job = store.create(path.name, key)
process_job.delay(job["job_id"])

# 3. Watch it
last = None
while True:
    job = store.get(job["job_id"])
    if (job["status"], job["message"]) != last:
        print(f"  {job['progress']:3}%  {job['status']:12} {job['message'] or ''}")
        last = (job["status"], job["message"])
    if job["status"] in ("completed", "failed"):
        break
    time.sleep(0.3)

if job["status"] == "failed":
    print(f"\n  Error: {job['error']}")
    sys.exit(1)

# 4. Playback links for the A/B comparison (valid for 1 hour)
d = job["diagnostics"]
print(f"\n  Health score : {d['health_score']} ({d['grade']})   AI bypassed: {job['processing']['ai_bypassed']}")
print(f"  Stored at    : s3://{storage.bucket}/{job['output_ref']}")
print(f"\n  Original (open in your browser):\n  {storage.presign_download(key, path.name)}")
print(f"\n  Enhanced (open in your browser):\n  {storage.presign_download(job['output_ref'], path.stem + '_mastered.wav')}")
