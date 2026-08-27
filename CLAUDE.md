# Stoma Companion — investor demo build

Stoma measuring & automated wafer cutting system for TACKLE Medical (Cole Fields).
DryDock engagement per **docs/PRD v0.2 (signed 2026-08-21).pdf** — investor-demo scope only.
Phase plan: [docs/PLAN.md](docs/PLAN.md) · Tickets: [TASKS.md](TASKS.md) · Decisions: [docs/decisions.md](docs/decisions.md)
**Start here in a new session: [docs/STATUS.md](docs/STATUS.md)** — live hosts, deploy commands, accuracy results, what's blocked on whom.

## What the demo must prove

A patient records a video of their stoma on any phone; the system reconstructs it,
measures the base, generates a wafer outline, and a cutting machine produces the
wafer on the spot — end to end, within tolerance, with zero patient input beyond
recording the video.

## Hard constraints — never violate

- **±1 mm** dimensional tolerance on the stoma base measurement (FR-09).
- **≤2 min** measure-to-cut cycle target (FR-11).
- **3 mm grace ring** offset around the outline — must stay a configurable parameter, never hard-coded (FR-07).
- **No LiDAR dependency** — plain camera video only, so non-Pro iPhones and Android stay in scope (NFR-04).
- **Patient flow exposes nothing technical** — no mesh views, sliders, parameters, or fine-tuning. 48% of patients are over 60 (FR-13, NFR-05).
- **No compliance work in this phase** — HIPAA/PHI handling is explicitly deferred by agreement (NFR-07). Do not add auth, audit logging, or de-identification unasked.
- **Nothing that forces re-architecture later** — every tool choice must scale demo → production (NFR-02). Compliance later arrives via Supabase BAA, not a rebuild.

## Architecture (decided — see docs/decisions.md before relitigating)

```
web (+ Capacitor iOS/Android shells)
        │  upload video / poll job
        ▼
backend/  Python FastAPI on DigitalOcean ── Supabase (storage + job state)
        │  ffmpeg keyframes → enqueue reconstruction job
        ▼
worker-colmap/  COLMAP + OpenMVS on Linux/GPU   ← primary engine
worker-mac/     Apple PhotogrammetrySession CLI ← fallback, same queue contract
        │  mesh (OBJ) back to storage
        ▼
backend/  ArUco scale → base detection → perimeter → offset → G-code → GRBL send
```

- Reconstruction is a **queue worker behind a narrow contract: keyframe images in, mesh out**. Engines are swappable; nothing outside the worker may depend on which engine ran.
- Front ends talk **only** to the backend API. Never let a client depend on macOS, Swift, or a specific reconstruction engine.

## Repo layout

- `backend/` — Python service: API, job pipeline, all measurement maths, G-code, GRBL sender, verification log.
- `worker-colmap/` — Dockerized COLMAP reconstruction worker.
- `worker-mac/` — headless Swift fallback worker (extracted from legacy `PhotogrammetryProcessor.swift`). Kept warm, not default.
- `web/` — web app + Capacitor shells. Patient flow + backend demo view.
- `legacy-mac/` — Cole's original Mac app, **reference only**: source of truth for porting maths and for generating golden fixtures. Don't extend it; don't delete it. Build: open `legacy-mac/StomaScanner/StomaScanner.xcodeproj`, scheme **StomaCompanion**, My Mac (macOS 14+, Xcode 15+).
- `fixtures/` — test videos and golden outputs from the legacy app. Parity tests compare ported code against these.
- `docs/` — PRD, plan, decision log.

## Porting map (legacy Swift → Python)

| Legacy (legacy-mac/…) | Ports to |
|---|---|
| `SharedPhotogrammetry/VideoFrameExporter.swift` | ffmpeg keyframe extraction |
| `SharedPhotogrammetry/PhotogrammetryProcessor.swift` | worker-colmap (or worker-mac fallback) |
| `CompanionMac/ArUcoDetectorBridge.mm`, `MeshArUcoOrbitDetector.swift` | OpenCV ArUco in Python |
| `CompanionMac/BasePerimeterExtractor.swift`, `StomaShapeMetrics.swift` | trimesh slicing / metrics |
| `CompanionMac/IdealFitOutlineGenerator.swift` | offset outline (configurable ring) |
| `CompanionMac/PlotterGcodeAutoExport.swift`, `PolarPathExport.swift` | G-code generation |
| `CompanionMac/ValidationExport.swift` | verification & test-log module |

## Working conventions

- Every ported stage lands with a **parity test against `fixtures/`** golden outputs. No fixture, no merge.
- New decisions of consequence get one line in `docs/decisions.md`, dated.
- Ticket-sized work: pick one item from `TASKS.md`, finish it, check it off in the same commit.
- Secrets (Supabase keys, DO tokens) live in untracked `.env` files — never commit them.
