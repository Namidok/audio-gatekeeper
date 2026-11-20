# Manual scripts

These are hands-on demo and integration scripts, kept from the build-up of the project.
They are **not** the test suite (that is `backend/tests/`, run with `pytest`), and several of
them talk to **real services**.

Run them from `backend/scripts/samples/` so they find their audio files, with `backend/` on the path:

```bash
cd backend/scripts/samples
PYTHONPATH=../.. python ../make_clip_tests.py
```

| Script | What it does | Needs |
|---|---|---|
| `benchmark.py` | Runs the whole pipeline on a folder of files and reports measured before/after numbers (see `docs/BENCHMARKS.md`). Run it from `backend/`. | nothing external |
| `make_*.py` | Write synthetic test signals (tones, clipped, band-limited, loud/quiet) into the current folder | nothing external |
| `make_test_versions.py` | Makes clipped and MP3-like versions of a song you pass in (use only audio you have rights to) | nothing external |
| `test_declip.py`, `test_resample.py`, `test_master.py`, `test_upsample.py`, `test_audiosr_adapter.py` | Print-and-listen demos of single pipeline stages | the files from `make_*.py` |
| `check_device.py` | Shows which compute device (CUDA / MPS / CPU) the pipeline would use | nothing external |
| `enhance_file.py` | Runs the pipeline on one file and writes the result | nothing external |
| `test_job_store.py` | Writes and reads jobs in **your local Redis** | Redis |
| `test_storage.py` | Uploads, downloads and **deletes test objects in your S3 bucket** | S3 credentials in `backend/.env` |
| `test_api.py`, `submit_s3_job.py`, `check_waveforms.py` | Full end-to-end runs through the API, S3 and the worker | API + worker + Redis + S3 |

`samples/` holds the generated test audio (git-ignored).
