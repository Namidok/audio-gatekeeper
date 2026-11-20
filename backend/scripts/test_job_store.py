import threading

from app.job_store import JobStore

store = JobStore()
print("Redis reachable:", store.r.ping())

# 1. A job moving through its stages
job = store.create("my_song.mp3", "uploads/my_song.mp3")
job_id = job["job_id"]
print(f"Created job {job_id[:8]}...  status={job['status']}  progress={job['progress']}")
for status in ["downloading", "diagnosing", "enhancing", "mastering", "uploading"]:
    job = store.set_status(job_id, status)
    print(f"  -> {job['status']:12} progress={job['progress']}")
job = store.update(job_id, status="completed", output_ref="enhanced/my_song.wav",
                   processing={"steps_applied": ["master to -14 LUFS / -1 dBTP"]})
print(f"  -> {job['status']:12} progress={job['progress']}  output={job['output_ref']}")

# 2. A late message cannot drag a finished job back
job = store.set_status(job_id, "enhancing")
print(f"Late 'enhancing' after completion -> status is still {job['status']}")

# 3. Ten workers writing at the same moment: nothing gets lost
job2 = store.create("other.wav", "uploads/other.wav")["job_id"]
threads = [threading.Thread(target=store.update, args=(job2,), kwargs={f"field_{i}": i}) for i in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
saved = [k for k in store.get(job2) if k.startswith("field_")]
print(f"10 simultaneous updates -> {len(saved)} saved")

# 4. Jobs expire on their own
print(f"Expires in {store.r.ttl(store._key(job_id)) / 3600:.0f} hours")
print("Unknown job ->", store.get("does-not-exist"))
