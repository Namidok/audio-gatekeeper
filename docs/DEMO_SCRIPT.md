# 3-minute demo script

## Before the demo

1. Generate the demo files: `cd backend && python scripts/benchmark.py --synthetic --no-env`.
   They land in `backend/scripts/samples/benchmark/`. For a more musical demo, you can also run
   `python scripts/make_test_versions.py your_track.mp3` on a track you have rights to, such as a
   Pixabay-licensed song.
2. Start Redis, the API, the worker (prefork) and the dashboard (see the README). Check that the
   header shows **System ready**.
3. Open the dashboard in dark mode with **Expert details off**. Use headphones or good speakers,
   and check that the bucket's CORS rule allows your origin.

## Script

**0:00 to 0:20: the problem**

> "Platforms ingest millions of uploads. Some are clipped. Some are low-bitrate MP3s dressed up
> as WAV files. Today that's found by listeners. This checks every upload at ingest, explains what's
> wrong in plain language, repairs what it can, and is honest about what it can't."

**0:20 to 1:00: a clean track bypasses repair.** Drop `01_clean_master.wav`.

- Point at the timeline: *Analyse* finishes, then *Repair* says **"Skipped: no damage"**. That's
  the gatekeeper bypass: a pristine track is never touched by repair or AI.
- In the health check, point out **100, grade A** and "Only loudness and peaks were adjusted".
- In the before/after strip, show the loudness going from -14.7 to **-14.0 LUFS** and the true
  peak to **-1.19 dBTP**.

**1:00 to 1:50: a clipped track is de-clipped.** Drop `02_hard_clipped_+6dB.wav`.

- In the health check: "Light clipping: a few peaks are flattened".
- In the before/after strip, clipping goes from **0.14% to 0%**. The true peak goes from +1.5 dBTP,
  which would distort on lossy encoding, to -1.2 dBTP.
- In the player, look at the A waveform: the peaks touch the red full-scale lines. Play A, then
  press **B**. Level-match is on, so the comparison isn't won by loudness.
- Optional, for engineers: switch on **Expert details** and show the clip detection level. It is
  relative to the file's own peak, so `03_clipped_then_turned_down.wav` is caught too.

**1:50 to 2:40: the MP3 cutoff is visible in the spectrum.** Drop `05_mp3_like_cutoff_11k.wav`.

- In the health check: "High frequencies are cut off at 11.0 kHz, typical of a low-bitrate MP3".
- Play **A** and point at the live spectrum: the bars stop dead at about 11 kHz and the right side
  is empty.
- **Be explicit:** the *Repair* step shows "Highs not restored", and the note says why: "the AI
  model is not installed on this server".

  > "In production this goes to a GPU worker running AudioSR. We show 'not restored' instead of
  > pretending, because a quality gatekeeper that overclaims is worse than none."

**2:40 to 3:00: wrap up**

- Download the mastered 24-bit WAV.
- Mention the boundaries:
  - Files are deleted after 7 days, or on request.
  - Audio never passes through the API.
  - The pipeline runs about 20x real time on a laptop CPU.
  - The test suite runs with no cloud account.
- Close with the next step: run it on a sample of your ingest with blind listening tests and the
  GPU path enabled.

## Questions to be ready for

- **"Is -14 LUFS right for YouTube?"** YouTube publishes no official number. -14 is the
  widely measured value, and it's labelled "unofficial" in the profile. Spotify's numbers are
  official: -14 LUFS, -1 dBTP, and -2 dBTP for masters louder than -14 LUFS.
- **"Why not just use the 85% rolloff?"** It flags most normal music. It is still reported, but
  the decision uses a spectral-cliff detector: a 20 dB drop within 1 kHz.
- **"Does de-clipping always help?"** Against known originals it brought the destroyed samples
  closer in every test: 3.0–7.2 dB on a real song, 1.4–3.3 dB on synthetic material. Heavy
  clipping is only partly repaired. An earlier spline method made light clipping worse and was
  replaced because of that measurement. Clipping smeared by a lossy codec can't be detected.
- **"Why do MP3s sound the same?"** Without the GPU model, they are unchanged apart from volume
  (measured), and the product says "Highs not restored" instead of pretending.
- **"What about the AI model's licence?"** The code is MIT. The weights' licence is unconfirmed
  for commercial use, and that's flagged in `THIRD_PARTY_LICENSES.md`.
