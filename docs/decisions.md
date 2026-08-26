# Decision log

One line of context per decision. Add new entries at the top, dated. Don't
relitigate settled entries in build sessions — reopen them with Aaron/Blake first.

## D16 — 2026-08-26 · Review fixes before P4: correct wafer G-code, true polygon offset, widened engine contract, hardened queue
A code review of everything built so far (measurement maths, plumbing, web app)
found two outright bugs in the last mile — the G-code was ×1000 too large (mm mesh
multiplied by the legacy metre→mm factor) and it emitted the *base* perimeter rather
than the grace-ring wafer outline — plus a set of things that would have failed on
the first real video. All fixed in one pass so P4 starts from correct output:
**G-code** now works in mm end to end (`input_units` is explicit; a >150 mm coordinate
raises), cuts the Ideal-Fit ring, and is expressed per **dialect** (`grbl`: G21/G90/G17,
G92 work origin at the platter centre, safe-Z rapid to P1, plunge, cut, retract, M30;
`stoma-plotter`: the legacy program). Remedy's example file (P4-4) becomes a third
dialect, not a rewrite. The polar M200/M201 plan is retained for legacy parity only.
**Grace ring (FR-07)** is a true polygon buffer (shapely, round joins) resampled to the
outline's point count — exact clearance in concave (peanut/kidney) regions where the
Swift per-vertex method pushed points inward; the gate checks min *and* max, not the
mean. The Swift algorithm stays as `generate_legacy` for fixture parity.
**Measurement on real meshes**: crop to a region of interest around the marker
(background/table no longer dominate heights or loops); floor = the marker/skin plane,
not the mesh minimum; slice height and margin in mm; diameter is the exact max chord
over the raw loop (samples under-read by 0.1–0.3 mm); lens distortion is carried from
the engine and removed before triangulation; marker triangulation seeds from the median
of pairwise solutions and drops outlier views, weighted by marker pixel size; "up" is
the marker normal **refined** by a RANSAC fit of the peristomal skin near the card
(kept only when it agrees within 15°). Also fixed a port bug in
`point_in_polygon_2d` (negative denominator clamped) that pushed the polar origin —
the G-code centre — off the outline centroid by ~1.7 mm.
**Engine contract widened**: `reconstruct()` returns mesh **+ engine-neutral camera
poses** (`poses.json`); COLMAP's file parsing moved into `worker-colmap/`. The
measurement stage (`app/measure_stage.py`) consumes poses, so the Mac fallback is a
real drop-in: a `MeasurementWorker` finishes any `mesh_ready` job from storage.
**Queue hardening** (migration 0004): one generic atomic claim for every stage;
`attempts` + a stale-claim watchdog (requeue, then fail) so a dead worker never
strands a job; the keyframe stage is a queue stage (in-API thread by default, or
standalone), not a FastAPI BackgroundTask; hard timeouts on reconstruction and
measurement; paginated storage listing (the 100-item default silently dropped frames
beyond 100); artefact paths recorded before anything else can fail; server-side
`result` merge; `/health` reports queue counts + oldest claim age; the pipeline ends
at **`measured`** — `done` is reserved for the cut (P4). Errors are split into a
patient-safe `error` sentence (shown as-is) and a server-side `error_detail`.
**Web**: never simulates when a real API is configured; `failed` is a real error
state; the ±1 mm badge only shows when a truth value exists (it was `?? true`);
uploads keep the phone's container type; retries only on network/5xx; poll ceiling;
scan id persisted across a phone lock; all patient copy in one file, ≥16 px, no
engine names or deviation numbers on screen. Deps are pinned for the images
(`backend/requirements.txt`, Dockerfile ARG refs verified to exist); CI now covers
worker-colmap and web. Docker images were **not** rebuilt in this session (no daemon
available) — first deploy re-verifies them.

## D15 — 2026-08-26 · Live deploy: single CPU droplet now, GPU deferred; OpenMVS pinned v2.1.0
Deployed the whole backend (API + COLMAP/OpenMVS worker + Caddy auto-TLS) onto one
CPU droplet (159.65.233.200, HTTPS at 159-65-233-200.sslip.io) because the DO
account's GPU quota is 0 (needs a support request). Consequence: reconstruction runs
dense stereo on CPU, so the **≤2 min cycle target (FR-11) is not met on this box** —
acceptable for wiring the pipeline end to end; moving to a GPU droplet later is a
Dockerfile/host swap (the CUDA `Dockerfile` already exists), not a re-architecture,
so NFR-02 holds. OpenMVS pinned to **v2.1.0**: master requires OpenCV 4.8 + CGAL 6,
but Ubuntu 24.04 ships 4.6 + 5.6; v2.1.0 matches the OS libraries. Also fixed a
COLMAP→OpenMVS image-path bug in `pipeline.sh` (dense images weren't visible to
DensifyPointCloud under the MVS working folder — would fail on GPU too). CPU SfM
proven on a 13-image public set (11 calibrated, poses recovered); full mesh proof
in progress. The prior API droplet (143.244.169.119) vanished from the account
between sessions — not destroyed by us; consolidating onto one box also cuts cost.

## D14 — 2026-08-26 · P5-1/P5-2: verification run log + design-control export
The P2-1 harness becomes the deliverable. `public.runs` (migration 0003, applied +
RLS on) logs one row per measured run: measurement vs caliper truth at the FR-10
reference point, deviation, pass/fail vs ±1 mm, and the engine/config that produced
it (FR-19/FR-20). Backend `runlog.py` mirrors it (RunRecord + in-memory/Supabase
RunStore); `verify/report.py` aggregates (count, unique stomas, mean/max deviation,
margin) and exports **CSV + a formatted PDF** shaped for design-control evidence
(FR-21) — headline reads "N tests across M unique stomas, all within ±1 mm, average
margin X mm". `runs_from_scoreboard` bridges the diameter board into run records, so a
fixture sweep can be logged and reported (this is the P5-3 seeding path). PDF uses
fpdf2 (core latin-1 font → unicode sanitised). Live round-trip against Supabase
verified. Seeding with the *real* fixture set is P5-3, blocked on Cole's videos (P0-3).

## D13 — 2026-08-26 · Patient app: Vite+React+TS+Tailwind, custom dark design system, demo-first
The web app (`web/`, P3-1…P3-4) is Vite + React + TypeScript + Tailwind with a
hand-built dark design system (no UI framework) — tokens in `tailwind.config.js` +
`src/index.css`, big rounded controls, teal accent on near-black, one action per
screen, plain language (FR-13/NFR-05). Flow: Welcome → Capture (MediaRecorder) →
Processing (progress ring + phase stepper) → Result (diameter + outline/wafer overlay
+ ±1 mm badge). It talks *only* to the backend API. Two run modes: with `VITE_API_BASE`
set it hits the real service; with it unset it runs a fully **simulated** flow (fake
progress + a sample measurement) so the app is demoable anywhere and reviewable now —
useful because the backend measurement result isn't wired until P1-10. Backend gained
permissive CORS (configurable, "*" for the demo phase; tighten before PHI). Capacitor
config is in place; native iOS/Android device passes (P3-6) + Cole's polish sign-off
(P3-5) need real devices/people and are deferred.

## D12 — 2026-08-26 · P2-5/P2-6: build the rigs now, get real numbers on real footage
Both tickets are empirical — the answers depend on COLMAP's behaviour on genuine
video — so what's built now is the *instrumentation*, not fabricated numbers.
**P2-5 keyframe minimization** (`stoma-keyframe-sweep`): reruns reconstruction +
measurement at N ∈ {20,50,100,350} frames, recording deviation-vs-truth and
runtime-vs-count with a table + ASCII plots + CSV. The engine is injected (the
`Reconstructor` contract), so it's validated here against a fake degrading engine and
runs for real on the worker-colmap image; when footage arrives the experiment is one
command. **P2-6 cycle-time budget** (`stoma-cycle-budget`): a `StageTimer` + a
`CycleReport` table against the 120 s target (FR-11) that names the bottleneck.
Keyframe extract and reconstruction now record their own seconds onto the job
(`result.timings_s`), so the first real video yields provisional per-stage numbers.
Expectation (to be confirmed): reconstruction dominates the budget. Real numbers +
tuning deferred with the footage (P0-3).

## D11 — 2026-08-26 · P2-3/P2-4: orientation fallback chain = ArUco → RANSAC → PCA; auto slice-height by area-profile junction
**P2-3 orientation fallbacks.** Added marker-free plane fits on the peristomal-skin
point cloud: RANSAC (robust to the stoma bump + reconstruction outliers) and PCA
(least-squares, biased). All three methods are scored on one synthetic board
(`stoma-score-orientation`); on clean synthetic data they're all sub-0.3°, so the
recommended **chain is by reliability preference, not raw error**: ArUco is primary
(the designed reference, when the marker is visible), RANSAC first fallback, PCA last.
A robustness test confirms RANSAC ≪ PCA under a one-sided outlier cluster.
**P2-4 automatic slice height.** The stoma rises out of the skin, so the
cross-section-area profile along the oriented axis has a broad skin region that drops
at the skin junction (FR-05). `auto_slice_fraction` finds that drop and slices just
above it; exposed as the `auto-height` method on the P2-1 diameter board. Demonstrated
on a synthetic stoma-on-thick-skin mesh where a fixed mid-slice lands in the skin
(~80 mm, fails) but auto-height recovers the 33 mm base. Junction-rule + orientation
sign tuning against real geometry deferred (P0-3); orientation still comes from params
here and will come from P2-2/P2-3 in the wired pipeline.

## D10 — 2026-08-26 · P2-2 marker-plane orientation validated on synthetic scenes; real-footage deferred
"Up" (FR-04) is recovered by triangulating the ArUco marker's corners across
multiple views (known camera poses) and fitting their plane — normal oriented toward
the cameras. Implemented as pure-numpy geometry (`measure/orientation.py`: pinhole
camera, DLT triangulation, SVD plane fit) so it's engine-agnostic: in production the
views/poses come from COLMAP; here from a synthetic renderer (`verify/synthetic.py`)
that warps a real cv2.aruco marker into views at *known* poses, so recovery is scored
against ground-truth normals (`stoma-score-orientation`). Synthetic recovery is
sub-0.1° across 0–30° tilts. No physical capture — real-footage validation is
deferred with the rest of the fixture work (P0-3). The recovered normal drops
straight into `slicing.extract_perimeter(normal=…)`; scoring it on the P2-1 *diameter*
board needs mesh+marker fixtures, so that wiring waits for P0-3 too.
The Swift measurement maths are ported to `backend/app/measure/` (slicing, metrics,
outline, gcode, aruco) as pure deterministic functions with synthetic-geometry
parity tests; fixture parity joins at P0-3. Scope calls, all faithful to the tickets:
- **P1-7 slicing** ports only the deterministic slice→perimeter→resample→diameter
  path. `BasePerimeterExtractor`'s automatic floor/opening-rim/orientation detection
  is explicitly P2 (P2-2…P2-4) and is *not* ported — the slice plane (up axis / tilt
  / spin / offset fraction) is a manual input here, per the P1-7 ticket. Base
  diameter = `feretMajor` ≈ `max_planar_chord_length`.
- **P1-6 ArUco** uses OpenCV `cv2.aruco` DICT_4X4_50 (the legacy hand-rolled decoder
  was verified against exactly that). The 3-D scale derivation (marker edge length in
  scene units → `scale = marker_side_mm / mean_side`, 12% CV gate) is ported and
  tested in pure numpy. Extracting the marker's 3-D corners *from the reconstructed
  mesh* (the legacy SceneKit orbit+unproject renderer) is a rendering pipeline tied
  to reconstruction context — wired at integration (P1-10), not reimplemented now.
- New backend deps live behind a `measure` extra (numpy, trimesh, opencv-headless)
  so the API image stays lean; the modules import them lazily.

## D8 — 2026-08-25 · Keyframe extractor defaults to the legacy 0.35 s interval, not the ticket's 0.1 s
P1-2's text said "default 1/0.1 s per current pipeline," but the legacy source of
truth (`VideoFrameExporter.swift`) uses `defaultIntervalSeconds = 0.35` (frame cap
350, clamped 0.03–1.0 s / 100–500 frames). Parity against legacy-generated golden
fixtures is a hard rule ("no fixture, no merge"), so the Python port defaults to
**0.35 s** to reproduce the legacy sampling schedule exactly. Interval + frame cap
stay configurable (env / `KeyframeParams`), so 0.1 s is a one-line change if P2
keyframe-minimization (P2-5) prefers it. Revisit when tuning quality vs frame count.

## D7 — 2026-08-25 · Infra is committed as code; live provisioning is a deliberate, separate step
P0-7 lands the Supabase schema (`supabase/migrations`), DigitalOcean droplet scripts
(`infra/digitalocean`), CI (`.github/workflows/ci.yml`) and `.env.example` as
infrastructure-as-code. It does **not** create live cloud resources: those spend
money on Aaron's accounts and need his credentials. The GPU worker droplet stays a
stub until P1-4/P1-5 (avoid idle GPU spend before COLMAP is proven). CI runs the
pure-maths + mocked-store tests (no Supabase, no GPU); fixture parity tests join
once P0-3 lands.

## D6 — 2026-08-25 · Device connectivity: simulator-first, nothing physical without machine info
Priority is the measurement pipeline (P1/P2/P5) proving ±1 mm on Cole's videos.
GRBL work follows, developed against grblHAL sim (or GRBL on a virtual serial
port) — no hardware purchased or built until Remedy's machine information arrives
(transport, example G-code). Any physical demo stand-in (~$100 bench rig) is a
client-approved expense decided with Cole at P6 planning, not started unilaterally.

## D5 — 2026-08-25 · Monorepo layout, legacy app preserved as fixture generator
Single repo (backend / workers / web / legacy-mac / fixtures / docs). Cole's Mac app
is kept read-only as the porting reference and the generator of golden fixtures;
original state preserved at git tag `as-received`.

## D4 — 2026-08-25 · Supabase + DigitalOcean
Supabase for storage/job state (chosen for the signed-BAA HIPAA upgrade path, so the
compliance phase is an upgrade, not a rebuild — NFR-03). Python/FastAPI on
DigitalOcean. Per PRD §5.1.

## D3 — 2026-08-25 · COLMAP-first reconstruction, Mac worker as fallback
Reconstruction sits behind the job queue as a swappable module (images in, mesh
out). Start with COLMAP + OpenMVS on Linux — the scalable end-state. Timeboxed P1
week-one quality gate: base-perimeter deviation vs caliper truth (±1 mm) on Cole's
test videos, through the same downstream Python code. If it misses, swap in Apple's
PhotogrammetrySession as a headless Swift worker on Aaron's machine polling the same
queue, and move COLMAP migration to the post-funding list. Rationale: Apple's engine
is the only component with demo evidence behind it, but it can't run on Linux;
the queue contract makes the engine choice reversible either way.

## D2 — 2026-08-25 · Web-first front end, Capacitor for iOS/Android
One web codebase wrapped with Capacitor rather than native apps. The legacy native
StomaScanner iOS target is retired. Browser capture quality must be proven in a P0
spike (MediaRecorder on real phones vs native capture) before the front end is
committed to it; Capacitor camera plugin is the fallback capture path.

## D1 — 2026-08-21 · Scope: investor demo only (PRD v0.2)
Compliance, patient records, clinician portal, hardware deferred by explicit
agreement (PRD §9). The demo must prove: video in → cut wafer out, ±1 mm, ≤2 min.
