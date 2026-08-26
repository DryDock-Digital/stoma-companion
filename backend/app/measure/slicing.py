"""Mesh slice → base perimeter → arc-length samples (P1-7).

Direct port of legacy CompanionMac/BasePerimeterExtractor.swift, restricted to the
deterministic path: the slice plane is supplied via *manual* parameters (up axis or
tilt + spin + slice-offset fraction). Automatic floor/orientation detection is P2
(P2-2…P2-4) and is intentionally not ported here — per the P1-7 ticket, "manual
slice params accepted as input at this stage."

Pipeline (matches the Swift `extract`):
  1. slice frame: normal n, in-plane basis (axisU, axisV), height bounds floor/max
  2. planeD = floorH + offset * span
  3. intersect every triangle with the plane → 2-D segments in (u, v)
  4. trace the largest closed loop from the segment soup
  5. re-origin at an interior "polar origin" → centroid in world space
  6. resample the loop to SAMPLE_COUNT equal-arc-length points
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SAMPLE_COUNT = 100  # BasePerimeterExtractor.sampleCount

FIXED_AXES: dict[str, tuple[float, float, float]] = {
    "positiveY": (0, 1, 0),
    "negativeY": (0, -1, 0),
    "positiveX": (1, 0, 0),
    "negativeX": (-1, 0, 0),
    "positiveZ": (0, 0, 1),
    "negativeZ": (0, 0, -1),
}


# --- small vector helpers --------------------------------------------------


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def _rot_x(deg: float) -> np.ndarray:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def _rot_y(deg: float) -> np.ndarray:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]], dtype=float)


def _rot_z(deg: float) -> np.ndarray:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def orthonormal_basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """In-plane basis for a plane with normal `n` (BasePerimeterExtractor.orthonormalBasis)."""
    n = _normalize(np.asarray(n, dtype=float))
    ref = np.array([0.0, 1.0, 0.0]) if abs(n[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = _normalize(np.cross(n, ref))
    v = _normalize(np.cross(n, u))
    return u, v


def slice_basis(n: np.ndarray, spin_degrees: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    u, v = orthonormal_basis(n)
    if abs(spin_degrees) <= 1e-6:
        return u, v
    rad = math.radians(spin_degrees)
    c, s = math.cos(rad), math.sin(rad)
    return _normalize(c * u + s * v), _normalize(-s * u + c * v)


def plane_normal_from_manual_tilt(
    base_axis: str, tilt_x: float, tilt_y: float, tilt_z: float
) -> np.ndarray:
    """Rz·Ry·Rx applied to a base axis (BasePerimeterExtractor.planeNormalFromManualTilt)."""
    base = _normalize(np.asarray(FIXED_AXES[base_axis], dtype=float))
    m = _rot_z(tilt_z) @ _rot_y(tilt_y) @ _rot_x(tilt_x)
    return _normalize(m @ base)


def height_extrema(vertices: np.ndarray, normal: np.ndarray) -> tuple[float, float]:
    """(min, max) of dot(vertex, normal). Floor = min in the deterministic path."""
    h = np.asarray(vertices, dtype=float) @ _normalize(np.asarray(normal, dtype=float))
    return float(h.min()), float(h.max())


# --- triangle ∩ plane ------------------------------------------------------


def intersect_triangle_plane(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
    n: np.ndarray,
    plane_d: float,
    axis_u: np.ndarray,
    axis_v: np.ndarray,
    epsilon: float,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Port of intersectTrianglePlaneSegments → list of (uv0, uv1) 2-D segments."""

    def uv(p: np.ndarray) -> np.ndarray:
        return np.array([float(p @ axis_u), float(p @ axis_v)])

    def signed(p: np.ndarray) -> float:
        return float(p @ n) - plane_d

    def on(h: float) -> bool:
        return abs(h) <= epsilon

    ha, hb, hc = signed(a), signed(b), signed(c)
    oa, ob, oc = on(ha), on(hb), on(hc)

    if oa and ob and oc:  # triangle coplanar → its three edges
        ua, ub, uc = uv(a), uv(b), uv(c)
        min_len = max(epsilon * 0.01, 1e-9)
        segs = [(ua, ub), (ub, uc), (uc, ua)]
        return [s for s in segs if np.linalg.norm(s[0] - s[1]) > min_len]

    hits: list[np.ndarray] = []

    def add_edge(p, q, hp, hq):
        if on(hp) and on(hq):
            if np.linalg.norm(p - q) > epsilon * 0.01:
                hits.append(p)
                hits.append(q)
            return
        if on(hp):
            hits.append(p)
            return
        if on(hq):
            hits.append(q)
            return
        if hp * hq < 0:
            t = hp / (hp - hq)
            hits.append(p + t * (q - p))

    add_edge(a, b, ha, hb)
    add_edge(b, c, hb, hc)
    add_edge(c, a, hc, ha)

    tol = max(epsilon * 20, 1e-8)
    uniq: list[np.ndarray] = []
    for p in hits:
        if not any(np.linalg.norm(u - p) < tol for u in uniq):
            uniq.append(p)

    min_seg = max(epsilon * 1e-3, 1e-9)
    if len(uniq) < 2:
        return []
    if len(uniq) == 2:
        p0, p1 = uv(uniq[0]), uv(uniq[1])
        return [(p0, p1)] if np.linalg.norm(p0 - p1) > min_seg else []
    # >2: pick the extremal pair in UV
    uvs = [uv(p) for p in uniq]
    best_i, best_j, best_d = 0, 1, 0.0
    for i in range(len(uvs)):
        for j in range(i + 1, len(uvs)):
            d = float(np.linalg.norm(uvs[i] - uvs[j]))
            if d > best_d:
                best_d, best_i, best_j = d, i, j
    return [(uvs[best_i], uvs[best_j])] if best_d > min_seg else []


# --- loop tracing ----------------------------------------------------------


class LoopError(RuntimeError):
    pass


def largest_perimeter_loop(
    segments: list[tuple[np.ndarray, np.ndarray]], snap_eps: float
) -> np.ndarray:
    """Port of largestPerimeterLoop: snap endpoints onto a grid, build an undirected
    adjacency, trace degree-2 loops, return the longest as an (N,2) array."""

    def snap(p: np.ndarray) -> tuple[float, float]:
        return (round(p[0] / snap_eps) * snap_eps, round(p[1] / snap_eps) * snap_eps)

    adj: dict[tuple[float, float], list[tuple[float, float]]] = {}

    def add_undirected(p, q):
        sp, sq = snap(p), snap(q)
        if math.dist(sp, sq) < snap_eps * 0.25:
            return
        adj.setdefault(sp, []).append(sq)
        adj.setdefault(sq, []).append(sp)

    for s0, s1 in segments:
        add_undirected(s0, s1)

    # Collapse duplicate neighbours (adjacent triangles share edges → inflated degree).
    for key in list(adj.keys()):
        unique: list[tuple[float, float]] = []
        for nb in adj[key]:
            if not any(math.dist(u, nb) < snap_eps * 0.15 for u in unique):
                unique.append(nb)
        adj[key] = unique

    best_loop: list[tuple[float, float]] = []
    best_len = 0.0
    max_steps = max(16_384, len(segments) * 2, len(adj) * 4)

    for start in adj:
        if len(adj.get(start, [])) != 2:
            continue
        loop = _trace_loop(start, adj, snap_eps, max_steps)
        if not loop:
            continue
        length = sum(math.dist(loop[i], loop[i + 1]) for i in range(len(loop) - 1))
        length += math.dist(loop[-1], loop[0])
        if length > best_len:
            best_len, best_loop = length, loop

    if not best_loop:
        raise LoopError("Could not trace a closed perimeter from the slice.")
    return np.array(best_loop, dtype=float)


def _trace_loop(start, adj, snap_eps, max_steps):
    nb = adj.get(start)
    if not nb or len(nb) != 2:
        return None
    close = max(snap_eps * 0.01, 1e-6)
    path = [start]
    prev = start
    cur = nb[0]
    for _ in range(max_steps):
        if math.dist(cur, start) < close and len(path) >= 2:
            return path
        path.append(cur)
        neighbors = adj.get(cur)
        if not neighbors:
            return None
        nxt = next((x for x in neighbors if math.dist(x, prev) > close), None)
        if nxt is None:
            return None
        if math.dist(nxt, start) < close:
            return path
        prev, cur = cur, nxt
    return None


# --- polar origin + resample ----------------------------------------------


def polygon_centroid_2d(poly: np.ndarray) -> np.ndarray:
    n = len(poly)
    if n < 3:
        return poly[0].copy() if n else np.zeros(2)
    a = cx = cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1]
        a += cross
        cx += (poly[i][0] + poly[j][0]) * cross
        cy += (poly[i][1] + poly[j][1]) * cross
    a *= 0.5
    if abs(a) < 1e-14:
        return poly.mean(axis=0)
    return np.array([cx / (6 * a), cy / (6 * a)])


def point_in_polygon_2d(p: np.ndarray, poly: np.ndarray) -> bool:
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        pi, pj = poly[i], poly[j]
        if (pi[1] > p[1]) != (pj[1] > p[1]):
            x_int = (pj[0] - pi[0]) * (p[1] - pi[1]) / max(pj[1] - pi[1], 1e-12) + pi[0]
            if p[0] < x_int:
                inside = not inside
        j = i
    return inside


def _bbox_center_2d(poly: np.ndarray) -> np.ndarray:
    lo = poly.min(axis=0)
    hi = poly.max(axis=0)
    return (lo + hi) * 0.5


def _interior_point_closest(target: np.ndarray, poly: np.ndarray) -> np.ndarray | None:
    lo = poly.min(axis=0)
    hi = poly.max(axis=0)
    best = None
    best_d = math.inf
    for iy in range(21):
        for ix in range(21):
            p = np.array([lo[0] + (hi[0] - lo[0]) * ix / 20, lo[1] + (hi[1] - lo[1]) * iy / 20])
            if not point_in_polygon_2d(p, poly):
                continue
            d = float(np.sum((p - target) ** 2))
            if d < best_d:
                best_d, best = d, p
    return best


def polar_origin_2d(loop: np.ndarray) -> np.ndarray:
    """Interior center: area centroid if inside, else bbox center, else nearest grid
    interior point (concave slices can push the centroid outside)."""
    centroid = polygon_centroid_2d(loop)
    if point_in_polygon_2d(centroid, loop):
        return centroid
    bc = _bbox_center_2d(loop)
    if point_in_polygon_2d(bc, loop):
        return bc
    best = _interior_point_closest(centroid, loop)
    return best if best is not None else centroid


def point_on_plane(u, v, plane_d, n, axis_u, axis_v) -> np.ndarray:
    denom = float(n @ n)
    anchor = (plane_d / max(denom, 1e-8)) * n
    return anchor + u * axis_u + v * axis_v


@dataclass
class BasePlaneSample:
    index: int
    theta: float
    r: float
    x: float
    y: float


def arc_length_resample(shifted: np.ndarray, count: int = SAMPLE_COUNT) -> list[BasePlaneSample]:
    """count points evenly spaced by distance along the closed outline
    (arcLengthResample100)."""
    m = len(shifted)
    edge_len = np.array([np.linalg.norm(shifted[i] - shifted[(i + 1) % m]) for i in range(m)])
    total = float(edge_len.sum())

    def _sample(index: int, p: np.ndarray) -> BasePlaneSample:
        return BasePlaneSample(
            index, math.atan2(p[1], p[0]), float(np.linalg.norm(p)), float(p[0]), float(p[1])
        )

    out: list[BasePlaneSample] = []
    for i in range(count):
        target = total * i / count
        walked = 0.0
        placed = False
        for e in range(m):
            el = float(edge_len[e])
            if walked + el >= target - 1e-7:
                t = (target - walked) / max(el, 1e-10)
                out.append(_sample(i, shifted[e] + t * (shifted[(e + 1) % m] - shifted[e])))
                placed = True
                break
            walked += el
        if not placed:
            out.append(_sample(i, shifted[0]))
    return out


# --- top-level extract -----------------------------------------------------


@dataclass
class PerimeterResult:
    samples: list[BasePlaneSample]
    centroid_world: np.ndarray
    axis_u: np.ndarray
    axis_v: np.ndarray
    plane_normal: np.ndarray
    plane_constant: float
    loop_vertex_count: int
    slice_offset_fraction: float

    def plane_xy(self) -> list[tuple[float, float]]:
        return [(s.x, s.y) for s in self.samples]


def max_planar_chord_length(samples: list[BasePlaneSample]) -> float:
    """Longest distance between any two samples — the 'longest diameter' of the
    outline (BasePerimeterExtractor.maxPlanarChordLength)."""
    pts = np.array([[s.x, s.y] for s in samples])
    if len(pts) < 2:
        return 0.0
    best = 0.0
    for i in range(len(pts)):
        d = np.linalg.norm(pts[i + 1 :] - pts[i], axis=1)
        if d.size:
            best = max(best, float(d.max()))
    return best


def extract_perimeter(
    vertices: np.ndarray,
    faces: np.ndarray,
    normal: np.ndarray,
    slice_offset_fraction: float,
    *,
    spin_degrees: float = 0.0,
    floor_h: float | None = None,
    max_h: float | None = None,
) -> PerimeterResult:
    """Slice a mesh (vertices (V,3), faces (F,3)) at a manual plane and return the
    resampled base perimeter. `normal` is the slice up-axis; `floor_h`/`max_h`
    default to the vertex height extrema along `normal`."""
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=int)
    n = _normalize(np.asarray(normal, dtype=float))

    if floor_h is None or max_h is None:
        fh, mh = height_extrema(vertices, n)
        floor_h = fh if floor_h is None else floor_h
        max_h = mh if max_h is None else max_h

    span = max(max_h - floor_h, 1e-6)
    offset = max(0.0, min(slice_offset_fraction, 1.0))
    plane_d = floor_h + offset * span
    axis_u, axis_v = slice_basis(n, spin_degrees)
    eps = max(span * 1e-5, 1e-6)

    segments: list[tuple[np.ndarray, np.ndarray]] = []
    for f in faces:
        segments.extend(
            intersect_triangle_plane(
                vertices[f[0]], vertices[f[1]], vertices[f[2]], n, plane_d, axis_u, axis_v, eps
            )
        )
    if not segments:
        raise LoopError("The slice plane did not intersect the mesh.")

    snap_eps = max(eps * 10, 1e-6)
    loop = largest_perimeter_loop(segments, snap_eps)
    if len(loop) < 3:
        raise LoopError("Slice outline too small/degenerate to resample.")

    origin = polar_origin_2d(loop)
    centroid_world = point_on_plane(origin[0], origin[1], plane_d, n, axis_u, axis_v)
    shifted = loop - origin
    samples = arc_length_resample(shifted)

    return PerimeterResult(
        samples=samples,
        centroid_world=centroid_world,
        axis_u=axis_u,
        axis_v=axis_v,
        plane_normal=n,
        plane_constant=plane_d,
        loop_vertex_count=len(loop),
        slice_offset_fraction=offset,
    )
