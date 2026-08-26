"""Directional widths for non-circular stomas (caliper spans every 5°)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.measure import shape


def _ellipse(a, b, rot_deg=0.0, n=200):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    x, y = a * np.cos(t), b * np.sin(t)
    r = math.radians(rot_deg)
    return np.column_stack([x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)])


def test_ellipse_widths_are_axis_lengths_regardless_of_orientation():
    for rot in (0.0, 33.0, 117.0):
        p = shape.profile(_ellipse(17.0, 13.0, rot))
        assert p.max_width_mm == pytest.approx(34.0, abs=0.05)
        assert p.min_width_mm == pytest.approx(26.0, abs=0.05)
        assert p.max_width_angle_deg == 0.0  # angles are relative to the long axis
        assert p.min_width_angle_deg == 90.0
        assert abs(((p.principal_axis_deg - rot) + 90) % 180 - 90) < 1.0
        assert len(p.widths_by_angle) == 36
        # width at 45° off the long axis of an ellipse: sqrt(a²cos²+b²sin²)*2
        w45 = dict(p.widths_by_angle)[45.0]
        assert w45 == pytest.approx(
            2 * math.hypot(17 * math.cos(math.pi / 4), 13 * math.sin(math.pi / 4)), abs=0.05
        )
    assert p.equivalent_diameter_mm == pytest.approx(2 * math.sqrt(17 * 13), abs=0.05)


def test_circle_is_flat():
    p = shape.profile(_ellipse(16.5, 16.5))
    assert p.max_width_mm - p.min_width_mm < 0.02
    assert p.perimeter_mm == pytest.approx(2 * math.pi * 16.5, rel=1e-3)


def test_profile_needs_three_points():
    assert shape.profile([(0, 0), (1, 1)]) is None
