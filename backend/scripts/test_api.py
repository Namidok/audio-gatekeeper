"""Plays the role of the browser: talks to the API, uploads to S3, polls the job."""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


print("GET /health ->", call("GET", "/health"))

# --- The happy path: exactly what the dashboard will do ----------------------
path = Path(sys.argv[1] if len(sys.argv) > 1 else "clipped.wav")
data = path.read_bytes()

status, upload = call("POST", "/uploads", {"filename": path.name, "size_bytes": len(data)})
print(f"POST /uploads -> {status}  key={upload['object_key']}")

put = urllib.request.Request(upload["upload_url"], data=data, method="PUT", headers=upload["headers"])
with urllib.request.urlopen(put) as r:
    print(f"PUT  to S3    -> {r.status}")

status, job = call("POST", "/jobs", {"object_key": upload["object_key"], "filename": path.name})
print(f"POST /jobs    -> {status}  job_id={job['job_id']}  status={job['status']}")

last = None
while job["status"] not in ("completed", "failed"):
    time.sleep(0.5)
    _, job = call("GET", f"/jobs/{job['job_id']}")
    if job["status"] != last:
        print(f"GET  /jobs/…  -> {job['progress']:3}%  {job['status']:12} {job['message'] or ''}")
        last = job["status"]

if job["status"] == "completed":
    print(f"\n  Health {job['diagnostics']['health_score']} ({job['diagnostics']['grade']}), "
          f"applied: {'; '.join(job['processing']['steps_applied'])}")
    print(f"  original_url: {job['original_url'][:70]}...")
    print(f"  enhanced_url: {job['enhanced_url'][:70]}...")
else:
    print("  Error:", job["error"])

# --- Things that must be refused ---------------------------------------------
print("\nRefusals:")
print("  .exe upload          ->", call("POST", "/uploads", {"filename": "virus.exe", "size_bytes": 100}))
print("  1 GB upload          ->", call("POST", "/uploads", {"filename": "huge.wav", "size_bytes": 10**9}))
print("  someone else's file  ->", call("POST", "/jobs", {"object_key": "outputs/abc/master.wav", "filename": "x.wav"}))
print("  job before uploading ->", call("POST", "/jobs", {"object_key": "uploads/" + "a" * 32 + "/source.mp3", "filename": "x.mp3"}))
print("  unknown job          ->", call("GET", "/jobs/" + "0" * 32))
