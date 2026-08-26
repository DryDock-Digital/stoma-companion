# backend

Python/FastAPI service: job API, keyframe extraction, and (later) the measurement
maths, G-code and GRBL sender. Talks to Supabase for job state + object storage.
Front ends and workers depend only on this service and the queue contract, never
on a specific reconstruction engine (CLAUDE.md).

## Layout

```
app/
  main.py        FastAPI app factory; store + keyframe worker thread on app.state
  config.py      env-driven settings (.env here or at the repo root; see ../.env.example)
  models.py      Job + API models; JobStatus mirrors the jobs table; ScanStatus hides internals
  store.py       JobStore protocol + InMemoryJobStore (dev/tests) + SupabaseJobStore
  errors.py      StageError: patient-safe `error` vs raw `error_detail`, per stage
  paths.py       canonical object keys in the `scans` bucket (video, keyframes, mesh, poses, gcode)
  keyframes.py   ffmpeg keyframe extractor — port of VideoFrameExporter (P1-2)
  pipeline.py    keyframe stage as a queue worker: pending → extracting → keyframes_ready
  keyframe_worker.py  `python -m app.keyframe_worker` — standalone keyframe stage
  queue.py       engine contract (mesh + poses), reconstruction/measurement pollers, watchdog
  measure_stage.py  the measurement stage: mesh + poses.json + keyframes → result + G-code object
  cycle_time.py  StageTimer + CycleReport — per-stage cycle-time budget (P2-6)
  runlog.py      verification run log — RunRecord + RunStore (P5-1)
  routes/scans.py  POST /scans, GET /scans/{id}
  measure/       ported measurement maths + P2 algorithms:
    aruco.py       ArUco detection (dictionary configurable) + scale derivation (P1-6)
    slicing.py     ROI crop, mesh slice → perimeter loop → samples; exact loop diameter (P1-7)
    metrics.py     feret/radial/perimeter/area/diameter (P1-7)
    outline.py     Ideal-Fit grace ring: true polygon buffer (FR-07); legacy port kept for parity
    gcode.py       wafer G-code per dialect (grbl | stoma-plotter); legacy polar plan (P1-8/P4)
    orientation.py camera model w/ distortion, robust triangulation, plane fits, skin refinement
    poses.py       engine-neutral poses.json (the second half of the engine contract)
    slice_height.py  area profile → auto base-slice height in mm above the skin plane (P2-4)
    measure_scan.py  the whole chain, parameterised by MeasureParams (job config)
  verify/        verification harnesses (P2-1…P2-4):
    fixtures.py    discover/load fixtures (mesh + truth + scale + params)
    harness.py     diameter board: methods baseline + auto-height → Scoreboard (±1 mm)
    __main__.py    the `stoma-score` CLI
    synthetic.py   synthetic ArUco scenes + skin point clouds at known poses (P2-2/P2-3)
    orientation.py orientation board: compare aruco/ransac/pca vs known normal
    keyframe_sweep.py  reconstruction-vs-frame-count experiment rig (P2-5)
    report.py      verification aggregates + CSV/PDF design-control export (P5-2)
tests/           pytest; no Supabase, no GPU, ffmpeg only for extractor integration
```

## Scoreboards, rigs & reports

```bash
stoma-score --method auto-height   # P2-1/P2-4: diameter vs caliper truth, ±1 mm, over fixtures/
stoma-score-orientation            # P2-2/P2-3: compare aruco/ransac/pca "up" recovery (synthetic)
stoma-keyframe-sweep --keyframes ./frames --truth 33.0   # P2-5: deviation + runtime vs frame count
stoma-cycle-budget run-timings.json                       # P2-6: per-stage budget vs the ≤2 min target
stoma-verification-report --out report                    # P5-2: run log → CSV + design-control PDF
```

The last two answer *empirical* questions and produce honest numbers only on real
footage: the sweep needs a reconstruction engine (COLMAP on the worker image) and the
budget reads per-stage timings recorded on the job (keyframe extract + reconstruction
already instrument themselves; `timings_s` on the job result). Validated here with a
fake engine / injected clock — no invented answers.

Both print per-item deviation + pass/fail + aggregates and exit non-zero on any
miss. Each P2 algorithm ticket reports on one of these boards: orientation methods
(ArUco plane, RANSAC, PCA) compare on the degrees board and yield a primary+fallback
chain; the auto-height slice method competes with the manual baseline on the mm
board. They grow into the P5 test-log module. See `fixtures/README.md` for the
diameter-board input schema.

The `measure/` package needs numpy/trimesh/opencv (the `measure` extra); it's kept
out of the base install so the API image stays lean, and imported lazily. Its
parity tests run on synthetic geometry with analytic answers now; fixture parity
(vs the legacy app's golden outputs) joins at P0-3.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scans` | upload a video → store it, create a `pending` job, queue keyframe extraction |
| `GET`  | `/scans/{id}` | job status + result |
| `GET`  | `/health` | liveness, Supabase on/off, queue counts + oldest claim age (stuck-job signal) |

Job lifecycle and the engine contract: [docs/queue-contract.md](../docs/queue-contract.md).
The pipeline ends at `measured` (G-code at `<job>/wafer.gcode`); `done` is the cut (P4).

## Run

```bash
pip install -e ".[dev]"          # needs Python 3.11+
uvicorn app.main:app --reload    # http://127.0.0.1:8000/docs
```

With no `SUPABASE_*` env set, the app falls back to an in-memory store — handy for
local poking and the entire test suite. Point it at Supabase by filling in
`../.env.example` values (see `../supabase/README.md`).

Docker:

```bash
docker build -t stoma-backend .
docker run -p 8000:8000 --env-file ../.env stoma-backend
```

## Test

```bash
ruff check . && ruff format --check . && pytest -q
```

Tests run against the in-memory store; `tests/test_store_contract.py` runs the same
contract against a live Supabase project when `STOMA_TEST_SUPABASE=1` (+ `SUPABASE_*`).
The parity-critical keyframe *sampling schedule* is pinned in `tests/test_keyframes.py`
without needing ffmpeg. Full fixture parity (frame counts vs golden manifests) lands
with P0-3. Docker images pin versions via `requirements.txt` (`pip install -c`).
