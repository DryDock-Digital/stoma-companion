"""P1-8 G-code port — structure of the emitted program and the polar plan."""

from __future__ import annotations

import math

import numpy as np

from app.measure import gcode
from app.measure.slicing import SAMPLE_COUNT, BasePlaneSample, PerimeterResult


def _circle_result(radius: float = 10.0):
    theta = np.linspace(0, 2 * math.pi, SAMPLE_COUNT, endpoint=False)
    samples = [
        BasePlaneSample(
            i, float(t), radius, float(radius * math.cos(t)), float(radius * math.sin(t))
        )
        for i, t in enumerate(theta)
    ]
    return PerimeterResult(
        samples=samples,
        centroid_world=np.zeros(3),
        axis_u=np.array([1.0, 0, 0]),
        axis_v=np.array([0, 1.0, 0]),
        plane_normal=np.array([0, 0, 1.0]),
        plane_constant=0.0,
        loop_vertex_count=SAMPLE_COUNT,
        slice_offset_fraction=0.5,
    )


def test_perimeter_gcode_structure():
    text = gcode.perimeter_gcode(_circle_result(), units_mm=True, user_scale=1.0)
    assert text.startswith("; Base perimeter — Stoma Companion")
    for token in ("G28", "G21", "G90", "M2"):
        assert token in text
    g1 = [ln for ln in text.splitlines() if ln.startswith("G1 X")]
    # first point + remaining 99 + explicit closing move back to first
    assert len(g1) == SAMPLE_COUNT + 1
    assert g1[0].endswith("F1200")


def test_meters_mode_omits_g21_and_feed():
    text = gcode.perimeter_gcode(_circle_result(), units_mm=False)
    assert "G21" not in text.splitlines()  # no standalone G21 command (comment may mention it)
    assert "F1200" not in text


def test_ideal_fit_header_present():
    text = gcode.ideal_fit_gcode([(1.0, 0.0)] * 4, _circle_result())
    assert "Ideal Fit" in text


def test_polar_plan_winds_once():
    plane_xy = [(s.x, s.y) for s in _circle_result().samples]
    plan = gcode.PolarPathExport.build(plane_xy)
    assert plan is not None
    assert math.isclose(plan.validation.winding_rad, 2 * math.pi, abs_tol=0.05)
    assert plan.validation.passes_winding

    text = gcode.PolarPathExport.polar_file_text(plan)
    assert text.startswith(";stoma_polar_v1")
    assert "M200" in text and "M202" in text
    assert text.count("M201 P") == len(plan.segments)
