# worker-mac — Apple PhotogrammetrySession fallback (P1-9, only if P1-5 misses)

Not built yet. This is the **exact** contract it has to honour so it drops in without
touching anything else (docs/queue-contract.md, decisions.md D3/D16):

1. Poll Supabase with the same store/RPC as `worker-colmap` (`claim_next_job(worker,
   "apple-photogrammetry", "keyframes_ready", "reconstructing")`); outbound only.
2. Download `<job>/keyframes/frame_*.jpg`, run `PhotogrammetrySession` (`.full` detail,
   `.modelFile` + `.poses` requests), export the mesh as **OBJ**.
3. Write **`poses.json`** (`backend/app/measure/poses.py` format, `stoma-poses-v1`): for
   every registered sample, `K` from the request's camera intrinsics, `R`/`t` as
   world→camera in OpenCV convention (Apple's transform is camera→world with a
   y-up/−z-forward camera: invert it, then flip the y and z rows), `dist: null`
   (Apple returns undistorted samples), `image_size`.
4. Upload `<job>/mesh.obj` and `<job>/poses.json`, set `mesh_path`/`poses_path`, mark
   `mesh_ready`. **Stop there** — the backend's `MeasurementWorker` (running on the
   COLMAP box, or anywhere with the `measure` extra) claims `mesh_ready` and does the
   rest. No measurement code on the Mac.
5. On failure: `failed` with a patient-safe `error` (`app.errors.DEFAULT_MESSAGES`),
   raw text in `error_detail`, `error_stage="reconstruct"`. The stale-claim watchdog
   covers a crashed process.

Heartbeat / worker-offline reporting (P1-9 acceptance) can reuse `queue_stats()`.
Source to port: `legacy-mac/SharedPhotogrammetry/PhotogrammetryProcessor.swift`.
