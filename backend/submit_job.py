"""Send one file to the worker and watch its progress.  Usage: python submit_job.py some_file.wav"""
import sys
import time
from pathlib import Path

from app.job_store import JobStore
from app.worker import process_job

path = Path(sys.argv[1]).resolve()
store = JobStore()
job = store.create(path.name, str(path))
process_job.delay(job["job_id"])                 # hand it to the worker and return immediately
print(f"Submitted {path.name} as job {job['job_id'][:8]}...  (this script is free to do other things now)")

last = None
while True:
    job = store.get(job["job_id"])
    state = (job["status"], job["message"])
    if state != last:
        print(f"  {job['progress']:3}%  {job['status']:12} {job['message'] or ''}")
        last = state
    if job["status"] in ("completed", "failed"):
        break
    time.sleep(0.2)

if job["status"] == "completed":
    d, p = job["diagnostics"], job["processing"]
    print(f"\n  Health score : {d['health_score']} ({d['grade']})   flags: {d['flags']}")
    print(f"  AI bypassed  : {p['ai_bypassed']}")
    print(f"  Applied      : {'; '.join(p['steps_applied'])}")
    print(f"  Output       : {job['output_ref']}")
else:
    print(f"\n  Error: {job['error']}")
