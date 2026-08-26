"""P1-7 StomaShapeMetrics port — validated against a circle of known radius."""

from __future__ import annotations

import math

import numpy as np

from app.measure import metrics


def _circle(radius: float, n: int = 64):
    # Half-step phase offset so no vertex lands on a cardinal axis (avoids the
    # ray-through-vertex degeneracy in radial distance — a sampling artifact, not
    # a port issue; real stoma outlines don't have axis-aligned vertices).
    theta = np.linspace(0, 2 * math.pi, n, endpoint=False) + math.pi / n
    return np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])


def test_requires_at_least_eight_points():
    assert metrics.compute(np.zeros((7, 2))) is None


def test_circle_metrics_match_geometry():
    r = 10.0
    m = metrics.compute(_circle(r))
    assert m is not None
    # diameter ≈ 2r, isotropic feret, perimeter ≈ 2πr, √area ≈ √(πr²), radial ≈ r
    assert math.isclose(m["feretMajor"], 2 * r, rel_tol=0.02)
    assert math.isclose(m["feretMinor"], 2 * r, rel_tol=0.02)
    assert math.isclose(m["perimeter"], 2 * math.pi * r, rel_tol=0.02)
    assert math.isclose(m["sqrtArea"], math.sqrt(math.pi) * r, rel_tol=0.02)
    assert math.isclose(m["radial90"], r, rel_tol=0.02)
    assert math.isclose(m.diameter, 2 * r, rel_tol=0.02)


def test_centroid_at_origin_for_centered_circle():
    m = metrics.compute(_circle(5.0))
    assert np.allclose(m.centroid, [0, 0], atol=1e-6)
