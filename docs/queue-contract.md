# Reconstruction queue contract (P1-3, widened 2026-08-26)

The reconstruction engine sits behind a job queue as a swappable module: **keyframe
images in, mesh + camera poses out** (CLAUDE.md architecture; decisions.md D3, D16).
Nothing outside a reconstruction worker may depend on which engine ran —
`worker-colmap/` (COLMAP + OpenMVS) and the fallback `worker-mac/` (Apple
PhotogrammetrySession) implement the *same* contract and are interchangeable at
runtime.

This document is authoritative. The SQL lives in
[`supabase/migrations/0001_jobs.sql`](../supabase/migrations/0001_jobs.sql) +
[`0004_queue_hardening.sql`](../supabase/migrations/0004_queue_hardening.sql); the
Python reference implementation is `backend/app/queue.py` and `backend/app/store.py`.

## Job state machine

```
 pending ─▶ extracting ─▶ keyframes_ready ─▶ reconstructing ─▶ mesh_ready
                                                                   │
                                            ┌──────────────────────┘
                                            ▼
                                   measuring ─▶ measured ─▶ cutting ─▶ done
                                                    ▲
                                    (pipeline ends here until P4 exists)

 (any state) ─▶ failed          [patient-safe `error`, raw `error_detail`, `error_stage`]
```

| Transition | Who | Trigger |
|---|---|---|
| create → `pending` | API | `POST /scans`: video stored, job row inserted, config stamped |
| `pending` → `extracting` → `keyframes_ready` | **keyframe worker** (`app.pipeline.KeyframeWorker`, in-API thread or standalone) | atomic claim; ffmpeg extraction (P1-2); frames uploaded |
| `keyframes_ready` → `reconstructing` | **reconstruction worker** | atomic claim |
| `reconstructing` → `mesh_ready` | **reconstruction worker** | mesh OBJ **and `poses.json`** uploaded |
| `mesh_ready` → `measuring` → `measured` | **measurement stage** (`app.measure_stage`, inline on the reconstruction worker *or* `MeasurementWorker`) | atomic claim; marker → scale/up → slice → outline → G-code object |
| `measured` → `cutting` → `done` | cutting worker (P4) | atomic claim; GRBL send |
| any → `failed` | whichever stage errored | `error` (patient sentence) + `error_detail` (raw) + `error_stage` |

Every stage claims with the **same** RPC: `claim_next_job(worker_id, engine, from, to)`
— `FOR UPDATE SKIP LOCKED`, bumps `attempts`, stamps `worker_id`/`claimed_at`. The
cutting stage (P4-2/3) is one more `(measured → cutting)` consumer of it.

**Clients treat `measured`, `done` and `failed` as terminal.** `done` is reserved for
"the wafer was cut".

## Storage layout (`scans` bucket)

Keys are owned by `backend/app/paths.py`:

```
<job_id>/input.mov
<job_id>/keyframes/frame_00000.jpg …          # + calibration_top.jpg
<job_id>/mesh.obj
<job_id>/poses.json                            # engine-neutral camera poses (below)
<job_id>/wafer.gcode                           # the cut program; never inlined in `result`
```

`SupabaseJobStore.list_objects` pages past Supabase's 100-item default — a 350-frame
keyframe set is listed in full.

## The engine interface

A reconstruction engine is anything satisfying `backend/app/queue.py::Reconstructor`:

```python
@dataclass
class ReconstructionOutput:
    mesh_path: Path                          # OBJ in the engine's own units
    cameras: dict[str, PinholeCamera]        # keyframe file name → pose, same frame as the mesh
    diagnostics: dict = {}                   # anything useful for the run log

class Reconstructor(Protocol):
    name: str                                # engine label
    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> ReconstructionOutput: ...
```

`reconstruct` reads the JPEGs in `keyframe_dir`, does its work in the scratch
`work_dir`, and returns the mesh **plus the poses of the keyframes it registered**
(`PinholeCamera`: K, world→camera R, t, optional OpenCV distortion, image size — see
`backend/app/measure/poses.py` for the JSON form). It must raise on failure (a
`StageError` with a patient-safe message, or anything — the worker maps it) and must
enforce its own wall-clock timeout.

Why poses are part of the contract: real-world **scale** and **up** come from
triangulating the ArUco card across views (P1-6, P2-2). That needs camera poses in
the mesh's frame; getting them from the engine is the only engine-agnostic way.
COLMAP converts `cameras.txt`/`images.txt` inside `worker-colmap/colmap_model.py`;
Apple's `PhotogrammetrySession` exposes per-sample transforms and intrinsics the Mac
worker converts the same way. The measurement stage never reads engine files.

## The worker loop

1. **Watchdog** (every ~60 s): `requeue_stale_jobs(claim_timeout_s, max_attempts)` —
   an in-progress row whose claim is older than the timeout goes back to its
   claimable state (`extracting→pending`, `reconstructing→keyframes_ready`,
   `measuring→mesh_ready`, `cutting→measured`); once `attempts` hits the cap it is
   failed with a "took longer than expected" message. A worker that dies mid-job
   therefore never strands a patient on a spinner.
2. **Claim** the next job for the stage.
3. **Download** the inputs; **run** the stage with its timeout.
4. **Upload** artefacts and record their paths *immediately* (a later measurement
   failure never hides a good mesh).
5. **Advance** the status; merge timings into `result.timings_s` via
   `patch_result` (server-side merge — stages in different processes don't clobber
   each other).
6. On any exception: `failed` with `error` (patient-safe), `error_detail`,
   `error_stage`. Writing the failure is itself guarded so a store blip can't crash
   the loop.

`engine` is a free-text label written for observability only. No consumer branches
on it.

## Configuration travels on the job

`POST /scans` stamps `Settings.measure_config()` (grace ring, tolerance, marker side,
ArUco dictionary, G-code dialect) plus keyframe params onto `jobs.config`. Every
stage reads the job, never the process environment, so a run is reproducible and
FR-07 stays a parameter end to end. Per-job overrides (e.g. `truth_mm`,
`slice_margin_mm`) are read by `MeasureParams.from_config`.
