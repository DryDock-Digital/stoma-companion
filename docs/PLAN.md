# Engagement plan — Measure to Cut

Phased plan for the investor-demo build, per PRD v0.2 (signed 2026-08-21).
Living version (shareable): https://claude.ai/code/artifact/f09c7821-340d-448d-97d4-ed6d899bea9c

**Frame:** budget ~$5k · no hard deadline (late-Oct competition is a soft marker) ·
±1 mm tolerance · ≤2 min cycle · compliance explicitly deferred.

## P0 — Intake & De-risk (~1 wk)

**TLDR:** Get the code under version control and building, reconcile the PRD against
Cole's design control document, and dispatch every open question that gates a later
phase. Includes the web-capture spike (phone-browser video must reconstruct as well
as native capture) and prep for the COLMAP quality gate.

## P1 — Backend Port (~2–3 wks)

**TLDR:** Lift the processing maths out of Swift into a hosted Python service with a
job API, proving each ported stage against golden fixtures from the legacy app.
Reconstruction starts on **COLMAP behind the job queue**, gated in week one on ±1 mm
against Cole's test videos — Apple's reconstructor on a Mac worker is the drop-in
fallback if it misses (see decisions.md D3).

## P2 — Auto Base Detection & Cycle Time (parallel with P1)

**TLDR:** Solve the core unsolved problem — the system has no notion of "up," so base
orientation and slice height are manual today. Prototype immediately against Cole's
test models; most promising shortcut is the ArUco marker plane ≈ base plane, giving
orientation for free. Wire the verification harness in early so every algorithm
change gets a deviation score automatically. Highest-risk item in the PRD.

## P3 — Patient App, Web + Capacitor (~2 wks, after P1 API stabilizes)

**TLDR:** One web codebase wrapped with Capacitor for iOS and Android: record a
video, wait, collect the wafer. Three screens, zero parameters, polished enough to
present to investors without caveats. Demo priority: web + one wrapped platform.

## P4 — Device Connectivity (deferred: after tolerance is proven)

**TLDR:** Generate G-code and stream it to a GRBL controller with no manual file
handling — developed entirely against a GRBL simulator (grblHAL sim), starting only
after the measurement pipeline proves ±1 mm on Cole's videos. Nothing physical is
bought or built until Remedy supplies machine information (transport, example
G-code); any demo-day bench stand-in is Cole's approval at P6 planning. Prefer
backend-over-Wi-Fi sending; Bluetooth would route through the Capacitor shells
(Web Bluetooth doesn't exist in iOS Safari).

## P5 — Verification & Test-Log Module (~1 wk to productize; harness starts in P2)

**TLDR:** Turn the test harness into the deliverable: every run logged with deviation
against ±1 mm, exportable in a form Cole can drop into his design control docs and
quote to investors — "N tests across N unique stomas at an average margin of X mm."

## P6 — Integration & Demo Rehearsal (final wk)

**TLDR:** Prove the claim end to end — within tolerance, under two minutes — then
rehearse until boring. Three consecutive clean runs, failure-mode drills, a recorded
fallback video, and the handoff package to Cole.

## Open questions on the clock

| Question | Owner | Blocks |
|---|---|---|
| Reference point for base-diameter truth measurement (FR-10) | Cole | P2 validation, P5 evidence quality |
| Wireless transport — Bluetooth, Wi-Fi, or both (FR-16) | Remedy | P4 final transport |
| Example G-code file / tool-specific parameters (FR-18) | Remedy | P4 output validation |
