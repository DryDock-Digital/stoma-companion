"""Ideal-Fit grace-ring offset (P1-8, FR-07) — CompanionMac/IdealFitOutlineGenerator.swift.

Offsets the base perimeter outward by a configurable clearance ring to produce the
wafer-cut outline. The ring defaults to 3 mm but is a parameter everywhere — never
hard-coded (FR-07).

Two algorithms:

  - `generate` (default) — a true polygon offset (Minkowski buffer with round joins,
    via shapely). The offset curve is at exactly `clearance_mm` from the base outline
    everywhere, including concave regions (peanut / kidney stomas), and can never
    self-intersect. The result is resampled to equal arc-length so the G-code chain
    has uniform spacing.
  - `generate_legacy` — the Swift algorithm (per-vertex averaged-tangent normal,
    sign-flipped away from the centroid, radial fallback on self-intersection). Kept
    for fixture parity only: in concave regions the centroid flip points the normal
    *inward*, so clearance goes negative and FR-07 is violated. Do not cut from it.

The clearance gate (SW-07) checks **min and max**, not the mean — a concavity that is
1 mm short would otherwise pass on average.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_CLEARANCE_MM = 3.0  # FR-07 grace ring (default only — always a parameter)
DEFAULT_TOLERANCE_MM = 1.0  # ±1 mm (FR-09)
DEFAULT_SAMPLE_COUNT = 100  # matches slicing.SAMPLE_COUNT

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
        """SW-07: every point of the wafer outline is within ±tol of the target
        clearance from the base outline."""
        lo, hi = self.target_mm - self.tolerance_mm, self.target_mm + self.tolerance_mm
        return lo <= self.min and self.max <= hi


@dataclass
class IdealFitResult:
    points: list[Point]
    clearance: ClearanceStats
    radial_fallback_indices: list[int]
    method: str = "buffer"

    @property
    def passes(self) -> bool:
        return self.clearance.passes


# --- geometry helpers ------------------------------------------------------


def _normalize2(v: Point) -> Point | None:
    length = math.hypot(v[0], v[1])
    if length <= 1e-8:
        return None
    return (v[0] / length, v[1] / length)


def _ring_centroid(ring: list[Point]) -> Point:
    n = len(ring)
    return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n)


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


def resample_closed(ring: list[Point], count: int = DEFAULT_SAMPLE_COUNT) -> list[Point]:
    """`count` points at equal arc-length spacing around a closed ring."""
    m = len(ring)
    if m < 2:
        return list(ring)
    edges = [math.dist(ring[i], ring[(i + 1) % m]) for i in range(m)]
    total = sum(edges)
    if total <= 0:
        return [ring[0]] * count
    out: list[Point] = []
    e = 0
    walked = 0.0
    for i in range(count):
        target = total * i / count
        while e < m - 1 and walked + edges[e] < target - 1e-9:
            walked += edges[e]
            e += 1
        t = (target - walked) / edges[e] if edges[e] > 1e-12 else 0.0
        a, b = ring[e], ring[(e + 1) % m]
        out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


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


# --- true polygon offset (default) -----------------------------------------


def generate(
    primary: list[Point],
    clearance_mm: float = DEFAULT_CLEARANCE_MM,
    tolerance_mm: float = DEFAULT_TOLERANCE_MM,
    *,
    sample_count: int | None = None,
) -> IdealFitResult | None:
    """Offset `primary` outward by `clearance_mm` using a proper polygon buffer.

    Handles concave outlines correctly (the offset is measured perpendicular to the
    nearest base edge, never toward/away from a centroid) and never self-intersects.
    Returns the outer boundary resampled to `sample_count` points (default: the
    number of input points, i.e. 100 for a slicing perimeter).
    """
    if len(primary) < 3:
        return None
    if clearance_mm < 0:
        raise ValueError("clearance_mm must be >= 0")
    from shapely.geometry import Polygon  # lazy: measure extra
    from shapely.validation import make_valid

    poly = Polygon(primary)
    if not poly.is_valid:
        poly = make_valid(poly)
        if poly.geom_type != "Polygon":
            # keep the largest polygon of a multi-part repair
            poly = max(getattr(poly, "geoms", [poly]), key=lambda g: g.area)
    # quad_segs: arc resolution of round joins; 16 per quarter-circle keeps the
    # polygonised arc within ~0.01 mm of a true 3 mm arc.
    buffered = poly.buffer(clearance_mm, quad_segs=16, join_style="round")
    if buffered.geom_type != "Polygon":
        buffered = max(buffered.geoms, key=lambda g: g.area)
    outer = [(float(x), float(y)) for x, y in buffered.exterior.coords[:-1]]
    # match the base outline's winding so the G-code chain runs the same direction
    if _signed_area(outer) * _signed_area(primary) < 0:
        outer.reverse()
    outer = _rotate_start_nearest(outer, primary[0])
    n = sample_count or len(primary)
    ideal = resample_closed(outer, n)
    stats = measure_clearance(primary, ideal, clearance_mm, tolerance_mm)
    return IdealFitResult(points=ideal, clearance=stats, radial_fallback_indices=[])


def _signed_area(poly: list[Point]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        a += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1]
    return 0.5 * a


def _rotate_start_nearest(ring: list[Point], anchor: Point) -> list[Point]:
    """Start the ring at the vertex nearest `anchor` so base + wafer outlines line up."""
    k = min(range(len(ring)), key=lambda i: math.dist(ring[i], anchor))
    return ring[k:] + ring[:k]


# --- legacy Swift algorithm (parity only) ----------------------------------


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
    if nx * vx + ny * vy < 0:  # flip to point away from centroid (wrong in concavities)
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


def generate_legacy(
    primary: list[Point],
    clearance_mm: float = DEFAULT_CLEARANCE_MM,
    tolerance_mm: float = DEFAULT_TOLERANCE_MM,
) -> IdealFitResult | None:
    """Port of IdealFitOutlineGenerator.generate — legacy-fixture parity only."""
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
    return IdealFitResult(
        points=ideal, clearance=stats, radial_fallback_indices=radial_fallback, method="legacy"
    )
