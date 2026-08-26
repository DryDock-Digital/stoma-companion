"""Ideal-Fit grace-ring offset (P1-8) — CompanionMac/IdealFitOutlineGenerator.swift.

Offsets the base perimeter outward by a configurable clearance ring to produce the
wafer-cut outline. The ring defaults to 3 mm but is a parameter everywhere — never
hard-coded (FR-07). Falls back to a radial offset where the local normal is
undefined, and to an all-radial offset if the mitred outline self-intersects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_CLEARANCE_MM = 3.0  # FR-07 grace ring
DEFAULT_TOLERANCE_MM = 1.0  # ±1 mm (FR-09)

Point = tuple[float, float]


@dataclass
class ClearanceStats:
    min: float
    mean: float
    max: float
    p95: float
    per_sample: list[float]
    target_mm: float
    tolerance_mm: float

    @property
    def passes(self) -> bool:
        """SW-07: mean within ±tol of target and max not over the upper bound."""
        lo, hi = self.target_mm - self.tolerance_mm, self.target_mm + self.tolerance_mm
        return lo <= self.mean <= hi and self.max <= hi


@dataclass
class IdealFitResult:
    points: list[Point]
    clearance: ClearanceStats
    radial_fallback_indices: list[int]

    @property
    def passes(self) -> bool:
        return self.clearance.passes


def _normalize2(v: Point) -> Point | None:
    length = math.hypot(v[0], v[1])
    if length <= 1e-8:
        return None
    return (v[0] / length, v[1] / length)


def _ring_centroid(ring: list[Point]) -> Point:
    n = len(ring)
    return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n)


def _outward_normal(cur: Point, prev: Point, nxt: Point, centroid: Point) -> Point | None:
    t1 = _normalize2((cur[0] - prev[0], cur[1] - prev[1]))
    t2 = _normalize2((nxt[0] - cur[0], nxt[1] - cur[1]))
    if t1 is None or t2 is None:
        return None
    tangent = _normalize2((t1[0] + t2[0], t1[1] + t2[1]))
    if tangent is None:
        return None
    nx, ny = -tangent[1], tangent[0]
    vx, vy = cur[0] - centroid[0], cur[1] - centroid[1]
    if nx * vx + ny * vy < 0:  # flip to point away from centroid
        nx, ny = -nx, -ny
    return (nx, ny)


def _radial_offset_point(p: Point, centroid: Point, distance: float) -> Point:
    d = _normalize2((p[0] - centroid[0], p[1] - centroid[1]))
    if d is None:
        return (p[0] + distance, p[1])
    return (p[0] + distance * d[0], p[1] + distance * d[1])


def _seg_intersect(a0: Point, a1: Point, b0: Point, b1: Point) -> bool:
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1, d2 = cross(a0, a1, b0), cross(a0, a1, b1)
    d3, d4 = cross(b0, b1, a0), cross(b0, b1, a1)
    if 0 in (d1, d2, d3, d4):  # legacy uses strict sign opposition (no touching)
        return False
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _self_intersects(ring: list[Point]) -> bool:
    n = len(ring)
    if n < 4:
        return False
    for i in range(n):
        a0, a1 = ring[i], ring[(i + 1) % n]
        for j in range(i + 2, n):
            if j == n - 1 and i == 0:
                continue
            b0, b1 = ring[j], ring[(j + 1) % n]
            if _seg_intersect(a0, a1, b0, b1):
                return True
    return False


def _dist_point_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _shortest_distance_to_polyline(p: Point, polyline: list[Point]) -> float:
    n = len(polyline)
    if n < 2:
        return 0.0
    return min(_dist_point_segment(p, polyline[i], polyline[(i + 1) % n]) for i in range(n))


def measure_clearance(
    primary: list[Point],
    ideal: list[Point],
    target_mm: float = DEFAULT_CLEARANCE_MM,
    tolerance_mm: float = DEFAULT_TOLERANCE_MM,
) -> ClearanceStats:
    distances = [_shortest_distance_to_polyline(p, primary) for p in ideal]
    if not distances:
        return ClearanceStats(0, 0, 0, 0, [], target_mm, tolerance_mm)
    s = sorted(distances)
    mean = sum(distances) / len(distances)
    p95_index = min(len(s) - 1, int(len(s) * 0.95))
    return ClearanceStats(
        min=s[0],
        mean=mean,
        max=s[-1],
        p95=s[p95_index],
        per_sample=distances,
        target_mm=target_mm,
        tolerance_mm=tolerance_mm,
    )


def generate(
    primary: list[Point],
    clearance_mm: float = DEFAULT_CLEARANCE_MM,
    tolerance_mm: float = DEFAULT_TOLERANCE_MM,
) -> IdealFitResult | None:
    """Offset `primary` outward by `clearance_mm`. Port of IdealFitOutlineGenerator.generate."""
    if len(primary) < 3:
        return None
    centroid = _ring_centroid(primary)
    ideal: list[Point] = []
    radial_fallback: list[int] = []
    n = len(primary)

    for i in range(n):
        prev = primary[(i - 1) % n]
        cur = primary[i]
        nxt = primary[(i + 1) % n]
        normal = _outward_normal(cur, prev, nxt, centroid)
        if normal is not None:
            ideal.append((cur[0] + clearance_mm * normal[0], cur[1] + clearance_mm * normal[1]))
        else:
            radial_fallback.append(i)
            ideal.append(_radial_offset_point(cur, centroid, clearance_mm))

    if _self_intersects(ideal):
        ideal = [_radial_offset_point(p, centroid, clearance_mm) for p in primary]
        radial_fallback = list(range(n))

    stats = measure_clearance(primary, ideal, clearance_mm, tolerance_mm)
    return IdealFitResult(points=ideal, clearance=stats, radial_fallback_indices=radial_fallback)
