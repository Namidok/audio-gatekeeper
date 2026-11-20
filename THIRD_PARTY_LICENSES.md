# Third-party licenses

This project is MIT-licensed (see [LICENSE](LICENSE)). It depends on the components below.
Licences were read from the installed package metadata (`importlib.metadata` and
`package-lock.json`) on 2026-11-18 and 2026-11-19. Re-check them when you upgrade.

**Summary.** Nothing below blocks commercial use by a platform company, **except that the
AudioSR model weights are unconfirmed**. The copyleft components (LGPL, MPL) are used as
unmodified, dynamically linked libraries in server-side software. Distributing container
images or binaries brings LGPL notice and relinking obligations.

## Needs attention

| Component | Licence | Note |
|---|---|---|
| **AudioSR model weights** (`haoheliu/audiosr_basic` on Hugging Face) | Tagged Apache-2.0, but the model card is empty and the training data isn't documented. A downstream conversion claims the weights are **CC-BY-NC-4.0** | **Get written confirmation from the author before any commercial use.** Optional: the product runs without it |
| AudioSR code (`haoheliu/versatile_audio_super_resolution`) | MIT | Optional GPU worker only. Its own dependencies (it pins numpy ≤ 1.23.5) were not audited here |
| NVIDIA CUDA libraries (in CUDA builds of torch and the `nvidia/cuda` image) | NVIDIA proprietary EULA | Redistribution in images is governed by NVIDIA's terms: review them before shipping images |

## Python (backend, `requirements.txt`)

| Package | Version | Licence |
|---|---|---|
| fastapi | 0.163.1 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| uvicorn (+ uvloop, httptools, watchfiles, websockets) | 0.54.0 | BSD-3-Clause (uvloop: Apache-2.0 / MIT) |
| pydantic, pydantic-core, pydantic-settings | 2.14.3 / 2.50.4 / 2.16.3 | MIT |
| celery, kombu, billiard, vine | 5.7.0 | BSD-3-Clause / BSD |
| redis | 8.1.0 | MIT |
| boto3, botocore, s3transfer | 1.44.3 | Apache-2.0 |
| numpy | 2.4.6 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0) |
| scipy | 1.17.1 | BSD-3-Clause |
| soundfile | 0.14.0 | BSD-3-Clause; **bundles libsndfile 1.2.2 (LGPL-2.1)** and its codec libraries (LGPL) |
| librosa | 0.11.0 | ISC |
| numba, llvmlite | 0.68.1 / 0.50.0 | BSD-2-Clause; Apache-2.0 with LLVM exception |
| scikit-learn, joblib, threadpoolctl | 1.9.1 | BSD-3-Clause |
| soxr | 1.1.0 | **LGPL-2.1-or-later** |
| pyloudnorm | 0.2.0 | MIT |
| torch | 2.15.0 | BSD-style (Apache-2.0, BSD, MIT, Zlib components); CUDA builds add NVIDIA libraries (above) |
| torchaudio | 2.11.1 | BSD-2-Clause |
| certifi | 2026.10.23 | **MPL-2.0** (file-level copyleft; unmodified use is fine) |
| requests, urllib3, idna, charset-normalizer | — | Apache-2.0 / MIT / BSD-3-Clause / MIT |
| Other transitive dependencies (anyio, click, jinja2, MarkupSafe, platformdirs, pooch, sympy, networkx, fsspec, filelock, …) | — | MIT / BSD / PSF |

Test-only (`requirements-dev.txt`): pytest (MIT), moto (Apache-2.0), fakeredis (BSD-3-Clause), httpx (BSD-3-Clause).

## JavaScript (frontend)

Production dependencies are only `next` 16.4.6 (MIT), `react` and `react-dom` 19.3.0 (MIT),
and their transitive packages. Across all 398 packages in `package-lock.json`, including dev:
318 MIT, 34 Apache-2.0, 16 ISC, 9 BSD and smaller permissive licences. The exceptions:

| Package | Licence | Note |
|---|---|---|
| `@img/sharp-libvips-*` (optional, pulled in by Next.js for image optimisation) | **LGPL-3.0-or-later** | Prebuilt, dynamically loaded. This app doesn't use `next/image` |
| `caniuse-lite` | CC-BY-4.0 | Browser-support data; attribution only |
| `axe-core` (dev only) | MPL-2.0 | Not shipped |

## Fonts (self-hosted in `frontend/app/fonts/`)

| Font | Licence | Source |
|---|---|---|
| Atkinson Hyperlegible Next | SIL Open Font License 1.1 (`OFL-AtkinsonHyperlegibleNext.txt`) | github.com/googlefonts/atkinson-hyperlegible-next, via the google/fonts repository |
| Martian Mono | SIL Open Font License 1.1 (`OFL-MartianMono.txt`) | github.com/evilmartians/mono, via the google/fonts repository |

## Test audio

All audio used by the tests and benchmarks is generated synthetically at run time. No
third-party recordings are included in the repository.
