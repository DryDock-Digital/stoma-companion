"""Measurement maths ported from the legacy Mac app (P1-6 … P1-8).

Pure, deterministic ports of the Swift/OpenCV pipeline, each with a parity test
against synthetic geometry now and against `fixtures/` golden outputs once P0-3
lands. Modules:

    aruco     — ArUco detection + scale derivation (P1-6)
    slicing   — mesh slice → perimeter loop → arc-length samples (P1-7)
    metrics   — StomaShapeMetrics: feret/radial/perimeter/area/diameter (P1-7)
    outline   — Ideal-Fit grace-ring offset (P1-8, FR-07)
    gcode      — perimeter G-code + polar path plan (P1-8)

These import numpy/opencv/trimesh, installed via the `measure` extra.
"""
