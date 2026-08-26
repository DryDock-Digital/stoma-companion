"""Directional widths of a base outline — the non-circular stoma measurement.

A stoma is rarely a circle, so one "diameter" cannot demonstrate ±1 mm (FR-09):
the longest chord can be exact while the narrow direction is 2 mm off. This
module measures the outline the way calipers do — the span between two parallel
jaws closed onto the shape — in every direction (5° steps), referenced to the
shape's own long axis so readings are repeatable regardless of how the phone or
the card was placed. Widest and narrowest are what a caliper naturally finds on a
physical model, so those are the two truths the verification log compares.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]


@dataclass
class ShapeProfile:
    max_width_mm: float
    max_width_angle_deg: float
    min_width_mm: float
    min_width_angle_deg: float
    equivalent_diameter_mm: float
    perimeter_mm: float
    area_mm2: float
    principal_axis_deg: float  # long axis in slice-plane coordinates
    widths_by_angle: list[tuple[float, float]]  # (deg from long axis, width mm)

    def to_json(self) -> dict:
        return {
            "max_width_mm": round(self.max_width_mm, 3),
            "max_width_angle_deg": round(self.max_width_angle_deg, 1),
            "min_width_mm": round(self.min_width_mm, 3),
            "min_width_angle_deg": round(self.min_width_angle_deg, 1),
            "equivalent_diameter_mm": round(self.equivalent_diameter_mm, 3),
            "perimeter_mm": round(self.perimeter_mm, 3),
            "area_mm2": round(self.area_mm2, 2),
            "principal_axis_deg": round(self.principal_axis_deg, 1),
            "widths_by_angle": [[round(a, 1), round(w, 3)] for a, w in self.widths_by_angle],
        }


def _area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _perimeter(pts: np.ndarray) -> float:
    return float(np.sum(np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)))


def caliper_width(pts: np.ndarray, angle_rad: float) -> float:
    """Span of the outline between two parallel jaws perpendicular to `angle_rad`
    (i.e. the extent of the shape *along* that direction)."""
    d = np.array([math.cos(angle_rad), math.sin(angle_rad)])
    proj = pts @ d
    return float(proj.max() - proj.min())


def principal_axis(pts: np.ndarray) -> float:
    """Direction (rad) of the outline's long axis: the caliper-width maximum,
    searched at 1° then refined — robust for near-circular shapes where a PCA
    axis is ill-defined."""
    angles = np.deg2rad(np.arange(0, 180, 1.0))
    widths = np.array([caliper_width(pts, a) for a in angles])
    best = int(np.argmax(widths))
    fine = np.deg2rad(np.arange(best - 1, best + 1.01, 0.1))
    return float(fine[int(np.argmax([caliper_width(pts, a) for a in fine]))]) % math.pi


def profile(outline, step_deg: float = 5.0) -> ShapeProfile | None:
    pts = np.asarray(outline, dtype=float)
    if len(pts) < 3:
        return None
    axis = principal_axis(pts)
    angles = np.arange(0.0, 180.0, step_deg)
    widths = [(float(a), caliper_width(pts, axis + math.radians(a))) for a in angles]
    w = np.array([x[1] for x in widths])
    imax, imin = int(np.argmax(w)), int(np.argmin(w))
    area = _area(pts)
    return ShapeProfile(
        max_width_mm=float(w[imax]),
        max_width_angle_deg=widths[imax][0],
        min_width_mm=float(w[imin]),
        min_width_angle_deg=widths[imin][0],
        equivalent_diameter_mm=2.0 * math.sqrt(area / math.pi),
        perimeter_mm=_perimeter(pts),
        area_mm2=area,
        principal_axis_deg=math.degrees(axis),
        widths_by_angle=widths,
    )
