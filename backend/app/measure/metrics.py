"""StomaShapeMetrics port (P1-7) — CompanionMac/StomaShapeMetrics.swift.

Shape descriptors of a 2-D base outline in the slice plane: Feret diameters,
radial distances, perimeter, √area, plus centroid and principal-axis angle. Units
are whatever the outline used (scene units before scaling, mm after). The primary
demo measurement — base diameter — is `feretMajor` (the longest Feret span) or,
equivalently for the demo, `slicing.max_planar_chord_length`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Relative weights for robust scale combination (StomaMetricKind.weight).
METRIC_WEIGHTS: dict[str, float] = {
    "feretMajor": 1.4,
    "feretMinor": 1.4,
    "feret45": 1.1,
    "feret135": 1.1,
    "perimeter": 1.0,
    "sqrtArea": 1.2,
    **{f"radial{a}": 0.7 for a in (0, 45, 90, 135, 180, 225, 270, 315)},
}


@dataclass
class ShapeMetrics:
    values: dict[str, float]
    centroid: np.ndarray
    principal_angle: float = field(default=0.0)

    def __getitem__(self, key: str) -> float | None:
        return self.values.get(key)

    @property
    def diameter(self) -> float:
        """Base diameter for the demo = longest Feret span."""
        return self.values.get("feretMajor", 0.0)


def _polygon_centroid(pts: np.ndarray) -> np.ndarray:
    n = len(pts)
    if n < 3:
        return pts[0].copy() if n else np.zeros(2)
    a = cx = cy = 0.0
    for i in range(n - 1):
        cross = pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
        a += cross
        cx += (pts[i][0] + pts[i + 1][0]) * cross
        cy += (pts[i][1] + pts[i + 1][1]) * cross
    a *= 0.5
    if abs(a) < 1e-14:
        return pts.mean(axis=0)
    return np.array([cx / (6 * a), cy / (6 * a)])


def _signed_area(pts: np.ndarray) -> float:
    a = 0.0
    for i in range(len(pts) - 1):
        a += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return a * 0.5


def _perimeter(pts: np.ndarray) -> float:
    return float(sum(np.linalg.norm(pts[i] - pts[i + 1]) for i in range(len(pts) - 1)))


def _principal_axis_angle(centered: np.ndarray) -> float:
    cxx = float(np.mean(centered[:, 0] ** 2))
    cyy = float(np.mean(centered[:, 1] ** 2))
    cxy = float(np.mean(centered[:, 0] * centered[:, 1]))
    return 0.5 * math.atan2(2 * cxy, cxx - cyy)


def _rotate(p: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * p[0] - s * p[1], s * p[0] + c * p[1]])


def _feret(aligned: np.ndarray, axis_angle: float) -> float:
    d = np.array([math.cos(axis_angle), math.sin(axis_angle)])
    proj = aligned @ d
    return float(proj.max() - proj.min())


def _ray_segment_intersect(origin, direction, a, b) -> float | None:
    v = b - a
    wo = a - origin
    denom = direction[0] * v[1] - direction[1] * v[0]
    if abs(denom) < 1e-12:
        return None
    t = (wo[0] * v[1] - wo[1] * v[0]) / denom
    s = (direction[0] * wo[1] - direction[1] * wo[0]) / denom
    if t >= 0 and 0 <= s <= 1:
        return t
    return None


def _radial_distance(aligned: np.ndarray, angle: float) -> float:
    d = np.array([math.cos(angle), math.sin(angle)])
    origin = np.zeros(2)
    best = 0.0
    for i in range(len(aligned) - 1):
        t = _ray_segment_intersect(origin, d, aligned[i], aligned[i + 1])
        if t is not None and t > best:
            best = t
    return best


def compute(points: list[tuple[float, float]] | np.ndarray) -> ShapeMetrics | None:
    """Port of StomaShapeMetrics.compute(from:). Needs >= 8 points."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 8:
        return None
    # Ensure closed for perimeter/area.
    if np.linalg.norm(pts[0] - pts[-1]) > 1e-6:
        pts = np.vstack([pts, pts[0]])

    centroid = _polygon_centroid(pts)
    centered = pts - centroid
    angle = _principal_axis_angle(centered)
    aligned = np.array([_rotate(p, -angle) for p in centered])

    values: dict[str, float] = {
        "feretMajor": _feret(aligned, 0.0),
        "feretMinor": _feret(aligned, math.pi / 2),
        "feret45": _feret(aligned, math.pi / 4),
        "feret135": _feret(aligned, 3 * math.pi / 4),
        "perimeter": _perimeter(pts),
        "sqrtArea": math.sqrt(max(abs(_signed_area(pts)), 0.0)),
    }
    radial_angles = {
        "radial0": 0.0,
        "radial45": math.pi / 4,
        "radial90": math.pi / 2,
        "radial135": 3 * math.pi / 4,
        "radial180": math.pi,
        "radial225": 5 * math.pi / 4,
        "radial270": 3 * math.pi / 2,
        "radial315": 7 * math.pi / 4,
    }
    for kind, th in radial_angles.items():
        values[kind] = _radial_distance(aligned, th)

    values = {k: v for k, v in values.items() if v > 1e-9}
    return ShapeMetrics(values=values, centroid=centroid, principal_angle=angle)
