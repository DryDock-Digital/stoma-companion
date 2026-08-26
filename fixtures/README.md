# Fixtures — golden baselines for parity testing

Each subdirectory is one physical test model, containing the input video and the
legacy Mac app's outputs for it. Ported Python stages must reproduce these within
tolerance — this is how the port is verified (see CLAUDE.md conventions).

## Schema (per model)

```
fixtures/<model-name>/
├── input.mov            # capture video (git LFS)
├── truth.json           # caliper measurement(s) at the agreed reference point (FR-10)
├── keyframes/           # frames as extracted by the legacy pipeline (optional, LFS)
├── mesh.obj             # legacy reconstruction output (LFS)
├── scale.json           # ArUco-derived scale factor
├── outline.json         # base perimeter points + derived diameter (mm)
└── output.gcode         # legacy G-code export
```

Generate via the legacy app (`legacy-mac/`, StomaCompanion scheme) — ticket P0-3.
Track video/mesh files with git LFS.

## Field schemas (read by the verification harness, P2-1)

`truth.json` — caliper truth at the FR-10 reference point:

```json
{ "reference_point": "FR-10 (TBD with Cole)", "metric": "diameter", "diameter_mm": 33.0 }
```

`scale.json` — scene units → mm (default `1.0` if absent):

```json
{ "scale_mm_per_scene_unit": 1000.0, "marker_side_mm": 20.0, "marker_id": 7 }
```

`params.json` *(optional)* — manual slice params for the baseline method (the P2-2+
auto methods derive orientation themselves, so they ignore this):

```json
{ "up_axis": "positiveZ", "slice_offset_fraction": 0.5, "spin_degrees": 0.0 }
```

`up_axis` is one of `positive/negative` × `X/Y/Z`; or supply `"tilt": {"base_axis",
"tilt_x","tilt_y","tilt_z"}` instead for an off-axis plane.

## Scoring

Once fixtures exist, score the pipeline against them in one command:

```bash
stoma-score                      # baseline method, ±1 mm, repo fixtures/
stoma-score --csv scores.csv     # also emit per-run CSV (P5 seed)
```

It prints a scoreboard (per-fixture deviation, pass/fail, mean/max |Δ|, margin) and
exits non-zero if any fixture is out of tolerance. Every P2 algorithm change reports
its score on this same board — see `backend/app/verify/`.
