# Decision log

One line of context per decision. Add new entries at the top, dated. Don't
relitigate settled entries in build sessions — reopen them with Aaron/Blake first.

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
