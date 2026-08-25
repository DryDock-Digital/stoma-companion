# Tickets

One ticket per Claude Code session. Each has acceptance criteria; check items off
in the same commit that completes them. Phases and constraints: docs/PLAN.md,
CLAUDE.md. FR/NFR references are to docs/PRD v0.2.

## P0 — Intake & De-risk

- [x] **P0-1 · Repo under version control** — git init, as-received commit + tag, monorepo layout, CLAUDE.md/PLAN/decisions/TASKS. *(done 2026-08-25)*
- [ ] **P0-2 · Legacy app builds & runs** — StomaCompanion scheme builds on a DryDock Mac; one test video processed end to end reproducing Cole's 19 Aug result (33 mm on the demo model). Document any signing/setup steps in legacy-mac/README.md.
- [ ] **P0-3 · Golden fixtures, first cut** — for each of Cole's test videos: run the legacy app, commit inputs + outputs (keyframes, mesh, scale, diameter, outline JSON, G-code) under `fixtures/<model>/`. Set up git LFS for video/mesh files. Acceptance: a README in fixtures/ documents the schema; at least one complete fixture set committed.
- [ ] **P0-4 · Emails out: Cole + Remedy** — draft and send the three open questions (FR-10 reference point → Cole; transport FR-16 + example G-code FR-18 → Remedy). Acceptance: sent, logged in docs/decisions.md when answered.
- [ ] **P0-5 · PRD ↔ design control reconciliation** — compare PRD v0.2 against Cole's design control document; list discrepancies; confirm pricing per §2. Output: docs/reconciliation.md.
- [ ] **P0-6 · Web-capture spike** — record stoma-model video via MediaRecorder in mobile Safari + Chrome/Android on real phones; run through the legacy reconstructor; compare mesh quality vs native-captured footage. Acceptance: written verdict in docs/decisions.md (browser capture OK / needs Capacitor camera plugin).
- [ ] **P0-7 · Infra skeletons** — Supabase project (storage bucket + `jobs` table), DigitalOcean droplet, CI running lint + tests on push. Secrets in .env.example pattern.

## P1 — Backend Port

- [ ] **P1-1 · Service scaffold** — FastAPI app in backend/: `POST /scans` (video upload → Supabase storage, job row `pending`), `GET /scans/{id}` (status + result). Dockerized. Tests against a local Supabase or mocked client.
- [ ] **P1-2 · Keyframe extraction** — ffmpeg-based extractor replacing VideoFrameExporter; frame rate configurable (default 1/0.1 s per current pipeline). Parity: frame count/timing vs fixtures.
- [ ] **P1-3 · Reconstruction queue contract** — define the worker contract (claim job → download keyframes → upload mesh OBJ → mark `mesh_ready`), as documented schema + Python reference poller. Engine-agnostic by construction.
- [ ] **P1-4 · COLMAP worker** — Dockerized COLMAP + OpenMVS in worker-colmap/ implementing the P1-3 contract on a GPU droplet. Acceptance: produces a mesh from fixture keyframes.
- [ ] **P1-5 · ⚑ COLMAP quality gate (timeboxed 3 days)** — run all fixture videos through COLMAP → downstream stages; judge base-perimeter deviation vs caliper truth. Within ±1 mm → COLMAP confirmed. Miss → activate P1-9 (Mac worker) and log in decisions.md. Cheap side-by-side with Meshroom/AliceVision while the harness is up.
- [ ] **P1-6 · ArUco scale port** — OpenCV ArUco detection + known-size scale derivation replacing ArUcoDetectorBridge/MeshArUcoOrbitDetector. Parity: scale factor vs fixtures within tolerance.
- [ ] **P1-7 · Slice → perimeter → diameter port** — trimesh-based cross-section, base-perimeter extraction, diameter (BasePerimeterExtractor, StomaShapeMetrics). Manual slice params accepted as input at this stage (P2 automates). Parity vs fixtures.
- [ ] **P1-8 · Offset outline + G-code port** — configurable grace ring (default 3 mm, FR-07) and G-code generation (PlotterGcodeAutoExport/PolarPathExport). Parity: G-code geometry vs fixtures.
- [ ] **P1-9 · Mac fallback worker (only if P1-5 misses)** — headless Swift CLI from PhotogrammetryProcessor.swift implementing the P1-3 contract, launchd-supervised on Aaron's machine, outbound polling only. Heartbeat row so the API can report worker-offline.
- [ ] **P1-10 · End-to-end pipeline test** — video POSTed to API returns scaled outline + G-code matching legacy output on the same input. This closes P1.

## P2 — Auto Base Detection & Cycle Time (parallel with P1)

- [ ] **P2-1 · Verification harness first** — script: given mesh + truth values, compute deviation vs ±1 mm; runs over all fixtures; one-command scoreboard. Every P2 ticket reports its score. (Grows into P5.)
- [ ] **P2-2 · Orientation via ArUco plane** — prototype: marker plane ≈ peristomal skin plane; its normal defines "up" (FR-04). Score across all fixtures.
- [ ] **P2-3 · Orientation fallbacks** — RANSAC plane-fit on surrounding skin surface; PCA. Compare against P2-2 on the scoreboard; pick primary + fallback chain.
- [ ] **P2-4 · Automatic slice height** — profile analysis along the oriented axis (cross-section area stabilization / curvature inflection at the skin junction) (FR-05). Score across all fixtures.
- [ ] **P2-5 · Keyframe minimization** — find the smallest frame count that keeps deviation within tolerance; measure reconstruction time vs frame count (FR-11).
- [ ] **P2-6 · Cycle-time budget** — measure each pipeline stage; produce a timing table against the ≤2 min target; identify the next bottleneck.

## P3 — Patient App (Web + Capacitor)

- [ ] **P3-1 · Web scaffold** — SPA (React or similar) against the backend API; Capacitor config for iOS + Android shells building empty.
- [ ] **P3-2 · Capture screen** — guided video recording (MediaRecorder or Capacitor camera per P0-6 verdict); large targets, one action per screen, no jargon (NFR-05).
- [ ] **P3-3 · Upload + progress flow** — upload with retry, job polling, progress states: uploading → reconstructing → measuring → cutting → done (FR-12). Network-drop handling defined.
- [ ] **P3-4 · Result / demo view** — "here is the measurement, here is the outline": measurement, outline overlay, deviation vs tolerance — investor-legible (FR-14). No parameters exposed anywhere in patient flow (FR-13).
- [ ] **P3-5 · Polish pass** — visual quality to demo standard (NFR-06); Cole sign-off.
- [ ] **P3-6 · Device matrix check** — same build verified in desktop browser, on an iPhone (Capacitor), and an Android phone (Capacitor) against the same API.

## P4 — Device Connectivity (deferred — starts after the measurement pipeline proves tolerance; see decisions.md D6)

Sequencing per D6: no GRBL work until P1/P2 demonstrate ±1 mm on Cole's videos,
and nothing physical is bought or built until machine information arrives.

- [ ] **P4-1 · GRBL simulator target** — stand up grblHAL sim (or GRBL on a virtual serial port) as the development target. Software only, no hardware.
- [ ] **P4-2 · G-code sender (vs sim)** — GRBL streaming module in backend/ (transport-agnostic interface so serial-over-Wi-Fi and Bluetooth-via-Capacitor both stay open per FR-16). Status: queued → cutting → done. Verified against the simulator.
- [ ] **P4-3 · Auto-transmit (vs sim)** — job completion triggers send with no manual file handling (FR-15); cut status surfaces in the P3 flow.
- [ ] **P4-4 · Machine info gate** — Remedy's answers in hand (transport FR-16, example G-code FR-18): validate our output against the example file, commit the transport, keep tooling specifics on the CNC side. **Blocked on Remedy — nothing physical before this.**
- [ ] **P4-5 · Demo cutting target decision (with Cole, at P6 planning)** — once P4-4 resolves: real machine available for demo day, or approve a ~$100 bench stand-in as a client expense. Cole's call, not started unilaterally.

## P5 — Verification & Test-Log Module

- [ ] **P5-1 · Run log schema** — per-run record in Supabase: video ref, derived measurement, caliper truth at the FR-10 reference point, deviation, pass/fail, engine + config used (FR-19, FR-20).
- [ ] **P5-2 · Aggregates + export** — count, mean/max deviation, margin; CSV + formatted PDF export shaped for design-control evidence (FR-21). Cole confirms it slots into his verification docs.
- [ ] **P5-3 · Seed the log** — full fixture set through the finished pipeline; honest dataset committed before the demo.

## P6 — Integration & Demo Rehearsal

- [ ] **P6-1 · End-to-end timed runs** — phone capture → hosted processing → auto base detection → G-code → bench cut; timed vs ≤2 min.
- [ ] **P6-2 · Demo script** — with Cole: which model, who holds the phone, what the demo view shows, where the verification log appears in the pitch.
- [ ] **P6-3 · Failure drills** — bad lighting, marker not detected, network drop: defined app behavior + presenter line for each.
- [ ] **P6-4 · Fallback recording** — one clean full run recorded for the live demo.
- [ ] **P6-5 · Handoff package** — repo access for Cole, deployment docs, seeded verification log, PRD §9 deferred list as the post-funding agenda.
- [ ] **P6-6 · Acceptance** — three consecutive clean runs, ≤2 min, within ±1 mm; Cole has rehearsed the demo himself.
