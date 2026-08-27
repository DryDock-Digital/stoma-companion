# ORBIT_Scanner Testing — benchmark notes (2026-08-27)

Source: Cole's June 2026 experiment set (`~/Downloads/ORBIT_Scanner Testing`, 44 clips):
distance 175/245 mm × elevation 20/40/60°, light low/med/high, phone 720p/1080p/4K, and
five early "ArUco Scaling" trials. All Model 1 (calipers 32.8 / 31.2 mm).

## Outcome of the first pass

**39 of the 44 clips contain no ArUco card** (the model sits on the mat alone), so the
pipeline stops at "couldn't see the square card" — correct behaviour, but it means these
clips cannot benchmark tolerance: scale and "up" come from the card by design.
The five "ArUco Scaling" trials do have a card; one (Demo1) measured 33.09 / 30.72 (PASS),
the other four are early trials of a different scene/scale and read nonsense.

Two things came out of it anyway:
- **Low-texture SfM failures** — the dim "light" clips register only 2–4 frames even at
  dense keyframes (white plate, glossy mat, no background texture). Fixed in the pipeline
  with a lower SIFT peak threshold, quadratic sequential overlap, looser mapper
  initialisation, and an automatic retry at 2× keyframes when < 50 % register.
- **Frame density vs. distance** — the 245 mm/40° clip registered all 65 frames at 0.35 s
  spacing but none at 40 spread frames; the retry above covers this case too.

Verified after the fixes (same clips, defaults): 245 mm/40° → 2/40 registered, automatic
retry at 80 frames → 81 registered (57 s); light=med → 41/40 registered on the first pass
(32 s). Both then stop at "no card", as they must.

## To benchmark tolerance across conditions

Re-film the same matrix **with the 27 mm card** (see docs/test-protocol.md), drop the
clips into the same folder layout, update `manifest.csv`, and run:

    stoma-bench run --manifest manifest.csv --root <folder> --api https://159-65-233-200.sslip.io
    stoma-bench report --state bench-state.json --api https://159-65-233-200.sslip.io --out results

Alternative (optional feature): a mat-as-scale fallback using the known diameter of the
brown disc, which would also rescue patient scans where the card slips out of view.
