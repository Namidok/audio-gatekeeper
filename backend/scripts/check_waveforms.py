"""Uploads a file through the API and prints the waveform data it gets back."""
import json
import sys
import time
import urllib.request
from pathlib import Path

API = "http://localhost:8000"


def call(method, path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                 method=method, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


path = Path(sys.argv[1])
data = path.read_bytes()
up = call("POST", "/uploads", {"filename": path.name, "size_bytes": len(data)})
urllib.request.urlopen(urllib.request.Request(up["upload_url"], data=data, method="PUT", headers=up["headers"]))
job = call("POST", "/jobs", {"object_key": up["object_key"], "filename": path.name})
while job["status"] not in ("completed", "failed"):
    time.sleep(0.5)
    job = call("GET", f"/jobs/{job['job_id']}")

for side, peaks in (job["waveforms"] or {}).items():
    at_ceiling = sum(p >= 0.99 for p in peaks)
    print(f"{side:9} {len(peaks)} points, loudest {max(peaks):.2f}, slices hitting the ceiling: {at_ceiling}")
