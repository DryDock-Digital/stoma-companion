# Reconstruction queue contract (P1-3)

The reconstruction engine sits behind a job queue as a swappable module: **keyframe
images in, mesh out** (CLAUDE.md architecture; decisions.md D3). Nothing outside a
reconstruction worker may depend on which engine ran — `worker-colmap/` (COLMAP +
OpenMVS) and the fallback `worker-mac/` (Apple PhotogrammetrySession) implement the
*same* contract and are interchangeable at runtime.

This document is authoritative. The SQL lives in
[`supabase/migrations/0001_jobs.sql`](../supabase/migrations/0001_jobs.sql); the
Python reference implementation is `backend/app/queue.py` (`ReconstructionWorker`)
and `backend/app/store.py`.

## Job state machine

```
 pending ─▶ extracting ─▶ keyframes_ready ─▶ reconstructing ─▶ mesh_ready
                                                                   │
                                            ┌──────────────────────┘
                                            ▼
                                   measuring ─▶ measured ─▶ cutting ─▶ done

 (any state) ─▶ failed          [error text recorded on the job]
```

| Transition | Who | Trigger |
|---|---|---|
| create → `pending` | API | `POST /scans`: video stored, job row inserted |
| `pending` → `extracting` → `keyframes_ready` | backend keyframe stage | ffmpeg extraction (P1-2); frames uploaded |
| `keyframes_ready` → `reconstructing` | **reconstruction worker** | atomic claim (see below) |
| `reconstructing` → `mesh_ready` | **reconstruction worker** | mesh OBJ uploaded |
| `mesh_ready` → `measuring` → … | backend measurement pipeline | P1-6…P1-8 (later) |
| any → `failed` | whichever stage errored | error string written to `jobs.error` |

Only the two **bold** rows are the reconstruction worker's responsibility. That's
the entire surface an engine must implement.

## Storage layout (`scans` bucket)

Keys are owned by `backend/app/paths.py`:

```
<job_id>/input.mov
<job_id>/keyframes/frame_00000.jpg …          # + calibration_top.jpg
<job_id>/mesh.obj
```

## The worker loop

1. **Claim.** Call `claim_next_job(worker_id, engine)`. It flips the oldest
   `keyframes_ready` job to `reconstructing` and stamps `worker_id`, `engine`,
   `claimed_at` — atomically, under `FOR UPDATE SKIP LOCKED`, so N workers never
   grab the same job. Returns nothing when the queue is empty.
2. **Download** every `frame_*.jpg` under `<job_id>/keyframes/`.
3. **Reconstruct.** Hand the keyframe directory to the engine; get back one OBJ.
4. **Upload** the OBJ to `<job_id>/mesh.obj`.
5. **Mark** the job `mesh_ready` with `mesh_path` set. On any failure, mark
   `failed` with the error text — never leave a job stuck in `reconstructing`.

`engine` is a free-text label (`'colmap+openmvs'`, `'apple-photogrammetry'`) written
for observability only. No consumer branches on it.

## The engine interface

A reconstruction engine is anything satisfying `backend/app/queue.py::Reconstructor`:

```python
class Reconstructor(Protocol):
    name: str                                             # engine label
    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> Path: ...
```

`reconstruct` reads the JPEGs in `keyframe_dir`, does its work in the scratch
`work_dir`, and returns the path to an OBJ. That's the whole contract — swapping
COLMAP for the Mac worker is a matter of passing a different `Reconstructor` to
`ReconstructionWorker`.

## Failure & recovery

- Errors are recorded on the job (`status='failed'`, `jobs.error`), not swallowed.
- Requeue policy (re-`keyframes_ready` a stale `reconstructing` job past a claim
  timeout) is deliberately **out of scope for the demo** — a stuck job is visible
  via `claimed_at` and handled operationally. Production hardening is post-funding.
- Heartbeat / worker-offline reporting arrives with the Mac fallback worker if
  P1-5 activates it (P1-9).
