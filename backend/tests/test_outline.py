"""P1-8 Ideal-Fit grace-ring port — clearance equals the configured ring (FR-07)."""

from __future__ import annotations

import math

import numpy as np

from app.measure import outline


def _circle(radius: float, n: int = 96):
    theta = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return [(radius * math.cos(t), radius * math.sin(t)) for t in theta]


def test_default_ring_is_three_mm():
    assert outline.DEFAULT_CLEARANCE_MM == 3.0


def test_offset_matches_default_clearance():
    primary = _circle(10.0)
    result = outline.generate(primary)
    assert result is not None
    assert math.isclose(result.clearance.mean, 3.0, abs_tol=0.15)
    assert result.passes  # within ±1 mm of 3 mm target
    # every offset point sits ~outside the original ring by the ring distance
    outer = [math.hypot(x, y) for x, y in result.points]
    assert all(o > 10.0 for o in outer)


def test_ring_is_configurable():
    primary = _circle(10.0)
    for ring_mm in (2.0, 5.0):
        result = outline.generate(primary, clearance_mm=ring_mm)
        assert math.isclose(result.clearance.mean, ring_mm, abs_tol=0.15)


def test_too_few_points_returns_none():
    assert outline.generate([(0, 0), (1, 0)]) is None
