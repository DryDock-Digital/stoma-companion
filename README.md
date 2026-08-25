# Stoma Companion

Stoma measuring & automated wafer cutting system — investor demo build.

**Client:** Cole Fields, TACKLE Medical · **Build:** DryDock, LLC
**Scope:** PRD v0.2, signed 2026-08-21 (`docs/`)

A patient records a video of their stoma on any phone. The system reconstructs a
3D mesh, derives real-world scale from an ArUco marker, finds the stoma base
automatically, generates an offset wafer outline, and streams G-code to a
GRBL-based cutter — end to end in under two minutes, within ±1 mm.

## Layout

| Directory | Contents |
|---|---|
| `backend/` | Python API + job pipeline + measurement maths + G-code/GRBL |
| `worker-colmap/` | COLMAP reconstruction worker (primary engine) |
| `worker-mac/` | Apple Object Capture fallback worker |
| `web/` | Web app + Capacitor iOS/Android shells |
| `legacy-mac/` | Original handoff Mac app — reference & fixture generator |
| `fixtures/` | Test videos + golden outputs for parity testing |
| `docs/` | PRD, phase plan, decision log |

Start with [CLAUDE.md](CLAUDE.md) (constraints + architecture), then
[docs/PLAN.md](docs/PLAN.md) and [TASKS.md](TASKS.md).

The original codebase as received from Cole is preserved at git tag `as-received`.
