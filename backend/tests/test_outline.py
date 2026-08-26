"""P1-8 Ideal-Fit grace ring (FR-07): the wafer outline sits at exactly the
configured clearance from the base *everywhere* — including concave stomas."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.measure import outline


def _circle(radius: float, n: int = 96):
    theta = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return [(radius * math.cos(t), radius * math.sin(t)) for t in theta]


def _peanut(n: int = 120):
    """Two overlapping lobes — a strongly concave waist, like a kidney/peanut stoma."""
    theta = np.linspace(0, 2 * math.pi, n, endpoint=False)
    r = 12.0 + 6.0 * np.cos(2 * theta)  # 18 mm on the lobes, 6 mm at the waist
    return [
        (float(ri * math.cos(t)), float(ri * math.sin(t))) for ri, t in zip(r, theta, strict=True)
    ]


def _min_dist_to_ring(p, ring):
    return outline._shortest_distance_to_polyline(p, ring)


def test_offset_circle_is_concentric_at_default_ring():
    primary = _circle(10.0)
    result = outline.generate(primary)
    assert result is not None
    outer = [math.hypot(x, y) for x, y in result.points]
    assert min(outer) == pytest.approx(13.0, abs=0.02)
    assert max(outer) == pytest.approx(13.0, abs=0.02)
    assert result.passes
    assert len(result.points) == len(primary)


def test_ring_is_configurable():
    primary = _circle(10.0)
    for ring_mm in (2.0, 3.0, 5.0):
        result = outline.generate(primary, clearance_mm=ring_mm)
        assert math.isclose(result.clearance.min, ring_mm, abs_tol=0.05)
        assert math.isclose(result.clearance.max, ring_mm, abs_tol=0.05)


def test_concave_outline_keeps_full_clearance():
    """In the waist the legacy vertex-normal method pushes points *inward*; the
    true buffer keeps ≥ 3 mm everywhere and the gate checks min, not mean."""
    primary = _peanut()
    result = outline.generate(primary, clearance_mm=3.0)
    assert result.clearance.min >= 2.95
    assert result.clearance.max <= 3.05
    assert result.passes
    # every base point is at least the ring away from the wafer outline too
    assert all(_min_dist_to_ring(p, result.points) >= 2.9 for p in primary)


def test_legacy_method_fails_concave_and_is_flagged():
    primary = _peanut()
    legacy = outline.generate_legacy(primary, clearance_mm=3.0)
    assert legacy.method == "legacy"
    # documents *why* it's parity-only: the waist loses clearance
    assert legacy.clearance.min < 2.5 or legacy.radial_fallback_indices


def test_clearance_gate_uses_min_and_max():
    stats = outline.ClearanceStats(
        min=1.5, mean=3.0, max=3.2, p95=3.1, per_sample=[], target_mm=3.0, tolerance_mm=1.0
    )
    assert not stats.passes  # mean is perfect, min is not
    stats2 = outline.ClearanceStats(
        min=2.9, mean=3.0, max=3.1, p95=3.1, per_sample=[], target_mm=3.0, tolerance_mm=1.0
    )
    assert stats2.passes


def test_winding_and_start_match_base():
    primary = _circle(10.0)
    result = outline.generate(primary)
    # same winding direction as the base (both CCW here)
    assert outline._signed_area(result.points) > 0
    # starts nearest the base's first point
    assert math.dist(result.points[0], (13.0, 0.0)) < 0.5


def test_too_few_points_returns_none():
    assert outline.generate([(0, 0), (1, 0)]) is None


def test_negative_ring_rejected():
    with pytest.raises(ValueError):
        outline.generate(_circle(10.0), clearance_mm=-1.0)
