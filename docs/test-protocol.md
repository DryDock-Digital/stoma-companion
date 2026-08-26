# Test protocol — measurement accuracy campaign (P1-5 gate)

Goal: enough real runs to say, with data, whether COLMAP meets **±1 mm on widest and
narrowest** and **≤ 60 s upload → measurement**. Passing this gate starts GRBL (P4).
Every run lands in the admin bench (`/admin`) and the verification log; nothing is
recorded by hand except the calipers.

## Before filming (once per model)

1. **Calipers.** Close the jaws across the stoma base *at the skin junction* (the
   agreed FR-10 reference), rotate to find the **widest** reading, then the
   **narrowest**. Two readings each; if they differ by > 0.3 mm take a third and use
   the middle. Write both down against the model name.
2. **Card.** Use the card printed by the Mac app (`LEGACY_4X4_50`). Caliper one
   edge of the black square — if it isn't 27.0 mm, set `MARKER_SIDE_MM` to what it is.

## Filming (every take)

- Card flat on the skin surface, 2–5 cm from the stoma, fully in view.
- Even light, no hard shadows across card or stoma. Phone 20–30 cm away, ~45° down.
- One slow full circle, ~15 s (the patient app stops at 15 s). Keep card + stoma in
  frame the whole way.
- **3 takes per model.** Name uploads `Model-X` with the same model name each time so
  the bench groups them.

## Running

- `/admin` → **New run** → video, model name, widest, narrowest → upload. Enter the
  calipers up front; they can be corrected on the run page later.
- Use **Re-run** on a run to repeat the same video (repeatability / speed-knob
  comparisons) without re-filming.
- Watch the stage timeline; total ≤ 60 s is the FR-11 evidence.

## Variants worth one take each

- Window light vs lamp vs dim room.
- Card at 2 cm and at 5 cm from the stoma.
- One deliberately bad take (fast orbit, card half out of frame) — the bench must
  show a plain-language failure, never a spinner.

## Reading the results

- Runs table: pass/fail per run (both readings within ±1 mm), aggregates row
  (mean/max |Δ|, pass count). CSV export for Cole via **report.csv**.
- Run page: width-by-direction chart (base and wafer), Ø-vs-height profile with the
  slice height marked, **Print 1:1 outline** to lay the model on paper.
- Same-model takes should agree within 0.5 mm; if they don't, look at
  `marker_views`, `marker_reprojection_px`, and `outline_method` first.

## Gate (write the verdict into docs/decisions.md)

- ≥ 3 models × 3 takes, every run passes; repeatability ≤ 0.5 mm; mean |Δ| ≤ 0.5 mm.
- Total ≤ 60 s on the GPU worker for 15 s clips.
- Failure drills give a patient-safe message.
→ then P4-1 (grblHAL simulator) begins. A miss on accuracy after the sweep (D18) →
  Mac PhotogrammetrySession worker (D3).
