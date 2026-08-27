# Status — where things stand (maintained; last update 2026-08-27)

Read this first in a new session. History and rationale: `docs/decisions.md` (D1–D19);
tickets: `TASKS.md`; test protocol: `docs/test-protocol.md`.

## Live system

| Piece | Where | How to deploy |
|---|---|---|
| API (FastAPI) + Caddy TLS | DO droplet `159.65.233.200` → https://159-65-233-200.sslip.io | `./infra/deploy.sh` |
| GPU reconstruction worker | `134.122.35.141` (client box, RTX 6000 Ada) | `WORKER_DOCKERFILE=Dockerfile ./infra/deploy-worker.sh` (`DETACHED_BUILD=1` for full rebuilds; `PULL_ONLY=1` once a registry is set) |
| CPU fallback worker | on the API droplet, container `stoma-worker`, **stopped** | `docker start stoma-worker` there |
| Data | Supabase project `kmvntwgakucckcolbgym` (`jobs`, `runs`, bucket `scans`), migrations 0001–0006 applied | `supabase/migrations/` |
| Patient app + admin bench | https://stomacompanion.netlify.app and `/admin` (auto-deploys from `main`) | push to `main` |

Settings that matter live in the root `.env` (gitignored): `MARKER_SIDE_MM=27`,
`KEYFRAME_TARGET_FRAMES=40`, `DENSE_ENGINE=openmvs`, `MESH_MODE=mesh`, full resolution.

## Pipeline (production defaults)

video → 40 keyframes spread over the clip (worker, one ffmpeg pass) → COLMAP (CUDA SIFT,
sequential matching, mapper) → OpenMVS-CUDA densify → mesh (decimate 0.3) → measurement
(LEGACY_4X4_50 card → scale + up; ROI around the card; stoma axis = plausible cluster nearest
the card; base = knee of the narrow-width profile above the skin junction; traced loop outline
with polar cross-check; caliper widths every 5°; 3 mm true-buffer wafer ring; GRBL G-code) →
`measured`. ~75 s upload→measured for a 15–30 s clip. `done` is reserved for the cut (P4).

## Accuracy so far

- **Model 1** (round; calipers 32.8 / 31.2 mm at the skin): all mesh-mode runs pass ±1 mm;
  production defaults +0.11 / −0.27 mm; repeatability ~0.1 mm (D19).
- **Model 2** (figure-8 ileostomy, `Demo2.mov`): clean two-lobe outline, 56.0 × 33.5 mm at the
  skin (2.98 mm up), 22 mm waist. **Needs base calipers** (widest end-to-end, narrowest across,
  at the skin) — lobe numbers given so far were taken at the lobes' equator.
- Point-cloud mode (`MESH_MODE=points`): 37–50 s but ~+1 mm wide (halo) — optional, not default.

## Gate to GRBL (P1-5)

≥ 3 models × 3 takes, every run within ±1 mm on widest and narrowest, repeatability ≤ 0.5 mm.
Status: 1 model graded. Blocked on (a) Model 2 base calipers, (b) more models / the June
condition matrix **re-filmed with the card** (39 of 44 June clips have no card — see
`docs/benchmarks/orbit-2026-06/README.md`), (c) a few browser-recorded scans from the patient
screen (P0-6) to confirm capture quality matches native-camera clips.

## Tools

- Admin bench: upload with model/calipers/tags, runs table with filters + group-by, run page
  with timeline, diagnostics, Ø-profile, width-by-direction, 1:1 printable outline, Re-run,
  Re-measure (from the stored mesh, seconds), Delete / Clear all.
- Batch: `stoma-bench run --manifest … --root … --api …` then `stoma-bench report`.
- Verification report: `stoma-verification-report` (CSV + PDF for design control).

## Open decisions / next steps

1. Image registry for disaster recovery (DO Basic $5/mo; wiring exists) — Aaron's call.
2. Model 2 base calipers → grade; then re-film the condition matrix with the card → batch.
3. Optional: mat-as-scale fallback (needs the disc diameter) for card-less scans.
4. After the gate: P4-1 grblHAL simulator; sender consumes `<job>/wafer.gcode`, claims
   `measured → cutting → done`.
