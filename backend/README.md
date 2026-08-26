# backend

Python/FastAPI service: job API, keyframe extraction, and (later) the measurement
maths, G-code and GRBL sender. Talks to Supabase for job state + object storage.
Front ends and workers depend only on this service and the queue contract, never
on a specific reconstruction engine (CLAUDE.md).

## Layout

```
app/
  main.py        FastAPI app factory; store + processor on app.state (swappable)
  config.py      env-driven settings (.env; see ../.env.example)
  models.py      Job + API models; JobStatus mirrors the jobs table
  store.py       JobStore protocol + InMemoryJobStore (dev/tests) + SupabaseJobStore
  paths.py       canonical object keys in the `scans` bucket
  keyframes.py   ffmpeg keyframe extractor — port of VideoFrameExporter (P1-2)
  pipeline.py    post-upload stage: pending → extracting → keyframes_ready
  queue.py       reconstruction contract + reference poller (P1-3)
  routes/scans.py  POST /scans, GET /scans/{id}
  measure/       ported measurement maths (P1-6…P1-8):
    aruco.py       ArUco detection + scale derivation (P1-6)
    slicing.py     mesh slice → perimeter loop → arc-length samples (P1-7)
    metrics.py     feret/radial/perimeter/area/diameter (P1-7)
    outline.py     Ideal-Fit grace-ring offset, FR-07 (P1-8)
    gcode.py       perimeter G-code + polar path plan (P1-8)
  verify/        verification harnesses (P2-1, P2-2):
    fixtures.py    discover/load fixtures (mesh + truth + scale + params)
    harness.py     diameter board: MeasurementMethod → Scoreboard (deviation vs ±1 mm)
    __main__.py    the `stoma-score` CLI
    synthetic.py   synthetic ArUco scenes (rendered views at known poses) — P2-2
    orientation.py orientation board: recovered "up" vs known normal; `stoma-score-orientation`
  measure/orientation.py   pinhole camera + triangulation + plane fit (P2-2)
tests/           pytest; no Supabase, no GPU, ffmpeg only for extractor integration
```

## Scoreboards

```bash
stoma-score                    # P2-1: diameter vs caliper truth, ±1 mm, over fixtures/
stoma-score-orientation        # P2-2: marker-plane "up" recovery vs known truth (synthetic)
```

Both print per-item deviation + pass/fail + aggregates and exit non-zero on any
miss. Each P2 algorithm ticket reports on one of these boards; they grow into the P5
test-log module. See `fixtures/README.md` for the diameter-board input schema.

The `measure/` package needs numpy/trimesh/opencv (the `measure` extra); it's kept
out of the base install so the API image stays lean, and imported lazily. Its
parity tests run on synthetic geometry with analytic answers now; fixture parity
(vs the legacy app's golden outputs) joins at P0-3.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scans` | upload a video → store it, create a `pending` job, queue keyframe extraction |
| `GET`  | `/scans/{id}` | job status + result |
| `GET`  | `/health` | liveness + whether Supabase is configured |

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

Tests run against the in-memory store; the parity-critical keyframe *sampling
schedule* is pinned in `tests/test_keyframes.py` without needing ffmpeg. Full
fixture parity (frame counts vs golden manifests) lands with P0-3.
