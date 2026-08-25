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
