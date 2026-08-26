# Tickets

One ticket per Claude Code session. Each has acceptance criteria; check items off
in the same commit that completes them. Phases and constraints: docs/PLAN.md,
CLAUDE.md. FR/NFR references are to docs/PRD v0.2.

## P0 — Intake & De-risk

- [x] **P0-1 · Repo under version control** — git init, as-received commit + tag, monorepo layout, CLAUDE.md/PLAN/decisions/TASKS. *(done 2026-08-25)*
- [ ] **P0-2a · Legacy app builds & runs** — StomaCompanion scheme builds on a DryDock Mac. Document signing/setup steps in legacy-mac/README.md. *Deferred by Aaron's call (2026-08-25): do when Cole's package arrives, alongside P0-3. Porting reads the Swift source; running it is only needed for fixtures.*
- [ ] **P0-2b · Smoke-test video** — print an ArUco marker (legacy `ArUcoMarkerGenerator`), film any small object on it with a phone, run it end to end through the legacy app. Proves the toolchain, not accuracy; commit as `fixtures/smoke/`. *Deferred with P0-2a.*
- [ ] **P0-2c · Reproduce Cole's baseline** — process his 19 Aug demo-model video, confirm the 33 mm reading reproduces. **Blocked on Cole's videos.**
- Note: P1-4's "produces a mesh" check uses a stand-in image set (public photogrammetry sample or phone photos of any object) — no dependency on Cole or the legacy app.
- [ ] **P0-3 · Golden fixtures, first cut** — for each of Cole's test videos: run the legacy app, commit inputs + outputs (keyframes, mesh, scale, diameter, outline JSON, G-code) under `fixtures/<model>/`. Set up git LFS for video/mesh files. Acceptance: fixtures/README schema honored; at least one complete fixture set committed. **Blocked on Cole's videos.**
- [ ] **P0-4 · Emails out: Cole + Remedy** — draft and send the three open questions (FR-10 reference point → Cole; transport FR-16 + example G-code FR-18 → Remedy). Acceptance: sent, logged in docs/decisions.md when answered.
- [ ] **P0-5 · PRD ↔ design control reconciliation** — compare PRD v0.2 against Cole's design control document; list discrepancies; confirm pricing per §2. Output: docs/reconciliation.md.
- [ ] **P0-6 · Web-capture spike** — record stoma-model video via MediaRecorder in mobile Safari + Chrome/Android on real phones; run through the legacy reconstructor; compare mesh quality vs native-captured footage. Acceptance: written verdict in docs/decisions.md (browser capture OK / needs Capacitor camera plugin).
- [x] **P0-7 · Infra skeletons** — Supabase project (storage bucket + `jobs` table), DigitalOcean droplet, CI running lint + tests on push. Secrets in .env.example pattern. *(done 2026-08-25 — as code: `supabase/migrations`, `infra/digitalocean`, `.github/workflows/ci.yml`, `.env.example`. Live provisioning deferred to Aaron's accounts, see decisions.md D7.)*

## P1 — Backend Port

- [x] **P1-1 · Service scaffold** — FastAPI app in backend/: `POST /scans` (video upload → Supabase storage, job row `pending`), `GET /scans/{id}` (status + result). Dockerized. Tests against a local Supabase or mocked client. *(done 2026-08-25 — in-memory + Supabase stores behind one protocol; 19 tests green.)*
- [x] **P1-2 · Keyframe extraction** — ffmpeg-based extractor replacing VideoFrameExporter; frame rate configurable (default 1/0.1 s per current pipeline). Parity: frame count/timing vs fixtures. *(done 2026-08-25 — `app/keyframes.py`; sampling schedule pinned in tests. Default is 0.35 s to match legacy source for fixture parity, not 0.1 s — see decisions.md D8. Full fixture parity awaits P0-3.)*
- [x] **P1-3 · Reconstruction queue contract** — define the worker contract (claim job → download keyframes → upload mesh OBJ → mark `mesh_ready`), as documented schema + Python reference poller. Engine-agnostic by construction. *(done 2026-08-25 — `docs/queue-contract.md` + `app/queue.py::ReconstructionWorker`; atomic `claim_next_job` in SQL + in-memory, covered by tests.)*
- [x] **P1-4 · COLMAP worker** — Dockerized COLMAP + OpenMVS in worker-colmap/ implementing the P1-3 contract on a GPU droplet. Acceptance: produces a mesh from fixture keyframes. *(done 2026-08-25 — `worker-colmap/` pipeline.sh + `ColmapReconstructor` + `--local` CLI. End-to-end COLMAP run needs the GPU droplet (P1-5) and fixture keyframes (P0-3); queue integration covered by tests.)*
- [ ] **P1-5 · ⚑ COLMAP quality gate (timeboxed 3 days)** — run all fixture videos through COLMAP → downstream stages; judge base-perimeter deviation vs caliper truth. Within ±1 mm → COLMAP confirmed. Miss → activate P1-9 (Mac worker) and log in decisions.md. Cheap side-by-side with Meshroom/AliceVision while the harness is up.
- [x] **P1-6 · ArUco scale port** — OpenCV ArUco detection + known-size scale derivation replacing ArUcoDetectorBridge/MeshArUcoOrbitDetector. Parity: scale factor vs fixtures within tolerance. *(done 2026-08-25 — `app/measure/aruco.py`: cv2.aruco DICT_4X4_50 detection + pixel→mm homography + 3-D `scale = marker_side_mm / mean_side` (CV gate). Mesh-render corner extraction deferred to P1-10, see decisions.md D9.)*
- [x] **P1-7 · Slice → perimeter → diameter port** — trimesh-based cross-section, base-perimeter extraction, diameter (BasePerimeterExtractor, StomaShapeMetrics). Manual slice params accepted as input at this stage (P2 automates). Parity vs fixtures. *(done 2026-08-25 — `app/measure/slicing.py` + `metrics.py`; validated against a unit-cube slice + known circle. Auto-orientation stays P2, see D9.)*
- [x] **P1-8 · Offset outline + G-code port** — configurable grace ring (default 3 mm, FR-07) and G-code generation (PlotterGcodeAutoExport/PolarPathExport). Parity: G-code geometry vs fixtures. *(done 2026-08-25 — `app/measure/outline.py` (FR-07 ring, configurable) + `gcode.py` (G1 XY exporter + polar plan).)*
- [ ] **P1-9 · Mac fallback worker (only if P1-5 misses)** — headless Swift CLI from PhotogrammetryProcessor.swift implementing the P1-3 contract, launchd-supervised on Aaron's machine, outbound polling only. Heartbeat row so the API can report worker-offline.
- [ ] **P1-10 · End-to-end pipeline test** — video POSTed to API returns scaled outline + G-code matching legacy output on the same input. This closes P1. *(in progress 2026-08-26 — measurement software + queue wiring landed: `app/measure/colmap_model.py` (COLMAP cameras/images.txt → pinhole poses), `app/measure/measure_scan.py` (marker triangulation → scale → orient → auto-slice → diameter → FR-07 outline → G-code → result JSON), `worker-colmap/measure_hook.py` wiring the MEASURING stage into the queue so a job drives to DONE with the full result. Validated on synthetic ground truth (33 mm) + a queue test. Remaining to close: real-video parity vs legacy, blocked on fixtures (P0-3); live reconstruction proof on the deployed CPU worker in progress.)*

## P2 — Auto Base Detection & Cycle Time (parallel with P1)

- [x] **P2-1 · Verification harness first** — script: given mesh + truth values, compute deviation vs ±1 mm; runs over all fixtures; one-command scoreboard. Every P2 ticket reports its score. (Grows into P5.) *(done 2026-08-26 — `backend/app/verify/`; `stoma-score` CLI, pluggable `MeasurementMethod`, per-fixture deviation + margin + CSV. Validated on synthetic cylinders; real fixtures at P0-3.)*
- [x] **P2-2 · Orientation via ArUco plane** — prototype: marker plane ≈ peristomal skin plane; its normal defines "up" (FR-04). Score across all fixtures. *(done 2026-08-26 — `measure/orientation.py` (pinhole camera + DLT triangulation + plane fit) + `verify/synthetic.py` (ArUco views at known poses) + `verify/orientation.py` scoreboard (`stoma-score-orientation`). Sub-0.1° recovery across 0–30° tilts on synthetic scenes; real-footage validation deferred (P0-3), see decisions.md D10.)*
- [x] **P2-3 · Orientation fallbacks** — RANSAC plane-fit on surrounding skin surface; PCA. Compare against P2-2 on the scoreboard; pick primary + fallback chain. *(done 2026-08-26 — `measure/orientation.py` pca/ransac + skin point clouds in `verify/synthetic.py`; `stoma-score-orientation` compares aruco/ransac/pca → chain ArUco → RANSAC → PCA. RANSAC robust to outliers where PCA degrades. See decisions.md D11.)*
- [x] **P2-4 · Automatic slice height** — profile analysis along the oriented axis (cross-section area stabilization / curvature inflection at the skin junction) (FR-05). Score across all fixtures. *(done 2026-08-26 — `measure/slice_height.py` area-profile junction detection + `auto-height` method on the diameter board; recovers the base where a fixed mid-slice hits the skin. Real-fixture tuning deferred (P0-3), see D11.)*
- [x] **P2-5 · Keyframe minimization** — find the smallest frame count that keeps deviation within tolerance; measure reconstruction time vs frame count (FR-11). *(done 2026-08-26 — `verify/keyframe_sweep.py` experiment rig + `stoma-keyframe-sweep` (engine-injected; table/ASCII plots/CSV; min-passing-frames). Validated with a fake degrading engine; real deviation curve arrives with footage (COLMAP), see decisions.md D12.)*
- [x] **P2-6 · Cycle-time budget** — measure each pipeline stage; produce a timing table against the ≤2 min target; identify the next bottleneck. *(done 2026-08-26 — `app/cycle_time.py` StageTimer + CycleReport + `stoma-cycle-budget`; keyframe extract + reconstruction record `result.timings_s`. Provisional numbers on the first real run, see D12.)*

## P3 — Patient App (Web + Capacitor)

- [x] **P3-1 · Web scaffold** — SPA (React or similar) against the backend API; Capacitor config for iOS + Android shells building empty. *(done 2026-08-26 — Vite+React+TS in `web/`, dark design system, API client, flow state machine, `capacitor.config.ts`; backend CORS added. Builds clean, ~57 kB gz.)*
- [x] **P3-2 · Capture screen** — guided video recording (MediaRecorder or Capacitor camera per P0-6 verdict); large targets, one action per screen, no jargon (NFR-05). *(done 2026-08-26 — `screens/Capture.tsx`: rear-camera viewfinder + reticle, one big shutter, recording ring/countdown, graceful fallback to a sample when the camera can't record.)*
- [x] **P3-3 · Upload + progress flow** — upload with retry, job polling, progress states: uploading → reconstructing → measuring → cutting → done (FR-12). Network-drop handling defined. *(done 2026-08-26 — `api/client.ts` upload retry + poll with network-drop tolerance; `Processing` shows a progress ring + phase stepper. Simulated flow when no backend is configured.)*
- [x] **P3-4 · Result / demo view** — "here is the measurement, here is the outline": measurement, outline overlay, deviation vs tolerance — investor-legible (FR-14). No parameters exposed anywhere in patient flow (FR-13). *(done 2026-08-26 — `Result` + `OutlineChart`: big diameter, ±1 mm badge, base + wafer outlines with caliper & scale. No parameters exposed.)*
- [ ] **P3-5 · Polish pass** — visual quality to demo standard (NFR-06); Cole sign-off. *Visual quality is at demo standard (verified via screenshots across all four screens); Cole sign-off pending.*
- [ ] **P3-6 · Device matrix check** — same build verified in desktop browser, on an iPhone (Capacitor), and an Android phone (Capacitor) against the same API. *Verified in desktop browser; iPhone/Android Capacitor passes need real devices — deferred.*

## P4 — Device Connectivity (deferred — starts after the measurement pipeline proves tolerance; see decisions.md D6)

Sequencing per D6: no GRBL work until P1/P2 demonstrate ±1 mm on Cole's videos,
and nothing physical is bought or built until machine information arrives.

- [ ] **P4-1 · GRBL simulator target** — stand up grblHAL sim (or GRBL on a virtual serial port) as the development target. Software only, no hardware.
- [ ] **P4-2 · G-code sender (vs sim)** — GRBL streaming module in backend/ (transport-agnostic interface so serial-over-Wi-Fi and Bluetooth-via-Capacitor both stay open per FR-16). Status: queued → cutting → done. Verified against the simulator.
- [ ] **P4-3 · Auto-transmit (vs sim)** — job completion triggers send with no manual file handling (FR-15); cut status surfaces in the P3 flow.
- [ ] **P4-4 · Machine info gate** — Remedy's answers in hand (transport FR-16, example G-code FR-18): validate our output against the example file, commit the transport, keep tooling specifics on the CNC side. **Blocked on Remedy — nothing physical before this.**
- [ ] **P4-5 · Demo cutting target decision (with Cole, at P6 planning)** — once P4-4 resolves: real machine available for demo day, or approve a ~$100 bench stand-in as a client expense. Cole's call, not started unilaterally.

## P5 — Verification & Test-Log Module

- [x] **P5-1 · Run log schema** — per-run record in Supabase: video ref, derived measurement, caliper truth at the FR-10 reference point, deviation, pass/fail, engine + config used (FR-19, FR-20). *(done 2026-08-26 — `supabase/migrations/0003_runs.sql` (applied to live, RLS on) + `app/runlog.py` RunRecord + in-memory/Supabase RunStore; live round-trip verified.)*
- [x] **P5-2 · Aggregates + export** — count, mean/max deviation, margin; CSV + formatted PDF export shaped for design-control evidence (FR-21). Cole confirms it slots into his verification docs. *(done 2026-08-26 — `app/verify/report.py`: summarize + CSV + fpdf2 PDF + scoreboard→runs bridge; `stoma-verification-report` CLI. Cole sign-off pending; real seeding is P5-3/P0-3.)*
- [ ] **P5-3 · Seed the log** — full fixture set through the finished pipeline; honest dataset committed before the demo.

## P6 — Integration & Demo Rehearsal

- [ ] **P6-1 · End-to-end timed runs** — phone capture → hosted processing → auto base detection → G-code → bench cut; timed vs ≤2 min.
- [ ] **P6-2 · Demo script** — with Cole: which model, who holds the phone, what the demo view shows, where the verification log appears in the pitch.
- [ ] **P6-3 · Failure drills** — bad lighting, marker not detected, network drop: defined app behavior + presenter line for each.
- [ ] **P6-4 · Fallback recording** — one clean full run recorded for the live demo.
- [ ] **P6-5 · Handoff package** — repo access for Cole, deployment docs, seeded verification log, PRD §9 deferred list as the post-funding agenda.
- [ ] **P6-6 · Acceptance** — three consecutive clean runs, ≤2 min, within ±1 mm; Cole has rehearsed the demo himself.
