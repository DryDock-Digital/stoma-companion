"""P1-8 / P4 G-code — coordinates are checked in millimetres, not just tokens.
(The old token-only test let a ×1000 units bug through.)"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.measure import gcode, outline
from app.measure.slicing import SAMPLE_COUNT, BasePlaneSample, PerimeterResult


def _circle_result(radius: float = 16.5):
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


def test_mm_input_is_not_rescaled():
    """A 33 mm base emits coordinates of ±16.5 mm — not ±16500."""
    text = gcode.perimeter_gcode(_circle_result(16.5), dialect=gcode.STOMA_PLOTTER)
    xy = gcode.parse_xy(text)
    assert len(xy) == SAMPLE_COUNT + 1
    radii = [math.hypot(x, y) for x, y in xy]
    assert max(radii) == pytest.approx(16.5, abs=1e-3)
    assert min(radii) == pytest.approx(16.5, abs=1e-3)


def test_legacy_metre_input_scales_by_1000():
    res = _circle_result(0.0165)  # legacy metre mesh
    text = gcode.perimeter_gcode(res, input_units="m")
    radii = [math.hypot(x, y) for x, y in gcode.parse_xy(text)]
    assert max(radii) == pytest.approx(16.5, abs=1e-3)


def test_units_mistake_is_rejected():
    with pytest.raises(gcode.GcodeUnitsError):
        gcode.perimeter_gcode(_circle_result(16.5), input_units="m")  # 16500 mm


def test_wafer_program_cuts_the_grace_ring_not_the_base():
    base = _circle_result(16.5)
    ideal = outline.generate(base.plane_xy(), clearance_mm=3.0)
    text = gcode.ideal_fit_gcode(ideal.points, base, clearance_mm=3.0, dialect=gcode.GRBL)
    radii = [math.hypot(x, y) for x, y in gcode.parse_xy(text) if (x, y) != (0.0, 0.0)]
    assert min(radii) == pytest.approx(19.5, abs=0.02)
    assert max(radii) == pytest.approx(19.5, abs=0.02)
    assert "Ideal Fit" in text and "clearance_mm = 3" in text


def test_stoma_plotter_dialect_structure():
    text = gcode.perimeter_gcode(_circle_result(), dialect=gcode.STOMA_PLOTTER)
    lines = text.splitlines()
    for token in ("G28", "G21", "G90", "M2"):
        assert token in lines
    g1 = [ln for ln in lines if ln.startswith("G1 X")]
    assert len(g1) == SAMPLE_COUNT + 1
    assert g1[0].endswith("F1200") and "Z0.000000" in g1[0]


def test_grbl_dialect_structure():
    text = gcode.perimeter_gcode(_circle_result(), dialect=gcode.GRBL)
    lines = text.splitlines()
    assert "G28" not in lines  # GRBL: G28 = stored position, never used
    assert lines.index("G21") < lines.index("G90") < lines.index("G17")
    assert "G92 X0 Y0" in lines  # work origin at platter centre
    safe_up = [ln for ln in lines if ln.startswith("G0 Z")]
    assert len(safe_up) == 2  # retract before rapid, retract after cut
    assert any(ln.startswith("G1 Z-1.000") for ln in lines)  # plunge
    rapid = [ln for ln in lines if ln.startswith("G0 X")]
    assert len(rapid) == 1  # one rapid to P1
    assert lines[-1] == "M30"
    assert "M200" not in text and "M201" not in text  # polar plan is not G-code
    assert not any("Z0.000000" in ln for ln in lines if ln.startswith("G1 X"))


def test_unknown_dialect_rejected():
    with pytest.raises(ValueError):
        gcode.get_dialect("marlin")


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
