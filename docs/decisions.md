# Decision log

One line of context per decision. Add new entries at the top, dated. Don't
relitigate settled entries in build sessions — reopen them with Aaron/Blake first.

## D9 — 2026-08-25 · Measurement port (P1-6…P1-8): deterministic core in Python, auto-orientation + mesh-render stay deferred
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
