# worker-colmap

Primary reconstruction engine: **COLMAP + OpenMVS**, keyframe JPEGs in → OBJ mesh
out. Implements the reconstruction half of
[docs/queue-contract.md](../docs/queue-contract.md). It is one interchangeable
implementation of the `Reconstructor` contract — the queue and every downstream
stage are engine-agnostic (decisions.md D3), so the Apple Mac worker (P1-9) is a
drop-in replacement if the P1-5 quality gate misses.

## Pieces

| File | Role |
|---|---|
| `pipeline.sh` | the COLMAP→OpenMVS command chain (feature → match → map → undistort → densify → mesh → texture → OBJ; poses exported as TXT into the work dir) |
| `reconstruct.py` | `ColmapReconstructor` — a `Reconstructor` that shells out to `pipeline.sh` with a hard timeout and returns mesh **+ poses** |
| `colmap_model.py` | COLMAP `cameras.txt`/`images.txt` → engine-neutral `PinholeCamera`s (intrinsics, pose, distortion) |
| `worker.py` | entrypoint: queue mode (reconstruct + measure inline, and finish any `mesh_ready` job from another engine), or `--local` one-shot |
| `Dockerfile` / `Dockerfile.cpu` | COLMAP (CUDA base / Ubuntu apt) + OpenMVS from source (pinned refs) + the backend package |
| `tests/` | contract tests that run without COLMAP (CI) |

The queue loop, job store and contract are **not** duplicated here — they're
imported from the backend `app` package so there's a single implementation.

## Build

Build from the **repo root** (the image needs both `backend/` and `worker-colmap/`):

```bash
docker build -f worker-colmap/Dockerfile -t stoma-worker .        # CUDA
docker build -f worker-colmap/Dockerfile.cpu -t stoma-worker .    # CPU droplet (D15)
```

Deploy to the droplet with `infra/deploy-worker.sh` (CPU by default).

Targets a CUDA GPU droplet; final sizing is decided at P1-5 when the quality
harness is up (see `infra/digitalocean/create-worker-droplet.sh`).

## Run

Queue mode — poll Supabase and reconstruct claimed jobs:

```bash
docker run --gpus all --env-file .env stoma-worker
```

Local mode — the P1-4 acceptance path, produce a mesh from a keyframe directory
with no queue (works on fixture keyframes once P0-3 lands):

```bash
docker run --gpus all -v "$PWD/frames:/in" -v "$PWD/out:/out" \
    stoma-worker python3 worker.py --local /in /out/mesh.obj
```

## Local dev without Docker

```bash
pip install -e ../backend      # brings in the queue contract (app package)
# needs colmap + OpenMVS tools on PATH
python worker.py --local ./frames ./out/mesh.obj
```

`reconstruct.py` raises a clear error if `colmap` isn't on PATH, so the wiring is
safe to import and exercise (via the backend's queue tests with a fake engine)
even on a machine without COLMAP.

## Status / acceptance

P1-4's acceptance is "produces a mesh from fixture keyframes." The code path is
complete and the queue integration is covered by `backend/tests/test_queue.py`
(fake engine). Running COLMAP end-to-end needs (a) the GPU droplet (provisioned at
P1-5, not before — avoids idle spend) and (b) fixture keyframes (P0-3, blocked on
Cole's videos). Until then, `--local` on any ordered photo set (the P1-4 note in
TASKS.md permits a public photogrammetry sample) exercises the full engine.
