"""Mesh slice → base perimeter → arc-length samples (P1-7).

Direct port of legacy CompanionMac/BasePerimeterExtractor.swift (the deterministic
slice → loop → resample path), plus what real reconstructions need on top:

  - `crop_mesh` — restrict the mesh to a region of interest around the marker before
    slicing. OpenMVS meshes include skin, table and background; without the crop the
    height extrema (and therefore every "fraction" height) are dominated by junk, and
    a slice near the skin traces the *skin* loop (longer than the stoma's) instead of
    the stoma. Cropping also turns the skin section into an *open* curve — only the
    stoma remains a closed loop — which is what makes "largest closed loop" correct.
  - `plane_h` — an absolute slice height along the normal (mm above the marker/skin
    plane), so callers don't have to express heights as fractions of a junk span.
  - the diameter is the max chord over the **raw loop vertices** (exact), not over the
    100 arc-length samples (which systematically under-reads by ~0.1–0.3 mm).
    `max_planar_chord_length` still accepts samples for legacy parity.

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


def crop_mesh(
    vertices: np.ndarray,
    faces: np.ndarray,
    center: np.ndarray,
    normal: np.ndarray,
    *,
    radius: float,
    below: float,
    above: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Keep faces with at least one vertex inside a cylinder of `radius` around the
    axis through `center` along `normal`, between heights `-below` and `+above`
    relative to `center` (straddling faces are kept so the crop boundary doesn't
    open holes in the surface). Returns (vertices, faces, inside_mask) with faces
    re-indexed; `inside_mask` flags the returned vertices that are strictly inside
    the region (use it for height extrema). Raises LoopError if nothing survives."""
    v = np.asarray(vertices, dtype=float)
    f = np.asarray(faces, dtype=int)
    n = _normalize(np.asarray(normal, dtype=float))
    c = np.asarray(center, dtype=float)
    rel = v - c
    h = rel @ n
    radial = np.linalg.norm(rel - np.outer(h, n), axis=1)
    inside = (radial <= radius) & (h >= -below) & (h <= above)
    keep_f = inside[f].any(axis=1)
    if not keep_f.any():
        raise LoopError("Region of interest around the marker contains no mesh.")
    used = np.unique(f[keep_f])
    remap = -np.ones(len(v), dtype=int)
    remap[used] = np.arange(len(used))
    return v[used], remap[f[keep_f]], inside[used]


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


#: Real reconstructions have holes; a section chain whose two ends are this close
#: (relative to its length) is treated as a loop and closed with a straight segment.
DEFAULT_MAX_GAP_FRAC = 0.15
#: Snap grid floor in scene units (mm in the wired pipeline). The legacy eps scales
#: with the mesh span (tuned for metre meshes); on a 30 mm stoma it collapses to
#: ~3e-4 mm and float noise from the reconstruction breaks every loop.
MIN_SNAP = 0.02


def largest_perimeter_loop(
    segments: list[tuple[np.ndarray, np.ndarray]],
    snap_eps: float,
    *,
    max_gap_frac: float = DEFAULT_MAX_GAP_FRAC,
) -> np.ndarray:
    """Port of largestPerimeterLoop: snap endpoints onto a grid, build an undirected
    adjacency, trace degree-2 loops, return the longest as an (N,2) array.

    Extension for real meshes: an *open* chain (two endpoints) whose gap is less than
    `max_gap_frac` of its length is also a candidate (closed across the gap) — a
    stoma section with a small hole at the skin junction. Long open arcs (skin cut
    by the region of interest) have a gap comparable to their length and are never
    picked. Closed loops win ties."""

    adj = _adjacency(segments, snap_eps)
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

    # open chains with a small gap (holes in the reconstruction)
    if max_gap_frac > 0:
        for chain in _open_chains(adj):
            length = sum(math.dist(chain[i], chain[i + 1]) for i in range(len(chain) - 1))
            gap = math.dist(chain[0], chain[-1])
            if length <= 0 or gap > max_gap_frac * length:
                continue
            if length + gap > best_len:
                best_len, best_loop = length + gap, chain

    if not best_loop:
        raise LoopError("Could not trace a closed perimeter from the slice.")
    return np.array(best_loop, dtype=float)


def candidate_loops(
    segments: list[tuple[np.ndarray, np.ndarray]],
    snap_eps: float,
    *,
    max_gap_frac: float = DEFAULT_MAX_GAP_FRAC,
) -> list[np.ndarray]:
    """All closed loops plus closable open chains from a slice, as (N,2) arrays."""
    adj = _adjacency(segments, snap_eps)
    max_steps = max(16_384, len(segments) * 2, len(adj) * 4)
    loops: list[list[tuple[float, float]]] = []
    seen_starts: set = set()
    for start in adj:
        if len(adj.get(start, [])) != 2 or start in seen_starts:
            continue
        loop = _trace_loop(start, adj, snap_eps, max_steps)
        if not loop:
            continue
        seen_starts.update(loop)
        loops.append(loop)
    if max_gap_frac > 0:
        for chain in _open_chains(adj):
            length = sum(math.dist(chain[i], chain[i + 1]) for i in range(len(chain) - 1))
            gap = math.dist(chain[0], chain[-1])
            if length > 0 and gap <= max_gap_frac * length:
                loops.append(chain)
    return [np.array(lp, dtype=float) for lp in loops if len(lp) >= 3]


def select_loop(
    loops: list[np.ndarray],
    *,
    containing: np.ndarray | None = None,
    min_perimeter: float = 0.0,
) -> np.ndarray:
    """Pick the section loop. Default: the longest (legacy). With `containing`
    (the stoma axis in slice coords): the **smallest** loop enclosing that point —
    the stoma section always lies inside the skin/mat loop, never the reverse —
    ignoring loops shorter than `min_perimeter` (noise blobs near the axis)."""
    if not loops:
        raise LoopError("Could not trace a closed perimeter from the slice.")

    def perim(lp: np.ndarray) -> float:
        return float(np.sum(np.linalg.norm(np.roll(lp, -1, axis=0) - lp, axis=1)))

    if containing is None:
        return max(loops, key=perim)
    c = np.asarray(containing, dtype=float)
    inside = [lp for lp in loops if perim(lp) >= min_perimeter and point_in_polygon_2d(c, lp)]
    if not inside:
        raise LoopError("No section loop encloses the stoma axis at this height.")
    return min(inside, key=perim)


def _open_chains(adj) -> list[list[tuple[float, float]]]:
    """Maximal degree-2 paths between endpoints (degree != 2 nodes)."""
    chains = []
    visited: set = set()
    for start, nbrs in adj.items():
        if len(nbrs) == 2 or not nbrs:
            continue
        for first in nbrs:
            if (start, first) in visited:
                continue
            path = [start, first]
            visited.add((start, first))
            prev, cur = start, first
            while len(adj.get(cur, [])) == 2:
                nxt = next(x for x in adj[cur] if x != prev)
                if nxt == start:  # closed after all
                    break
                path.append(nxt)
                prev, cur = cur, nxt
            visited.add((cur, prev))
            if len(path) >= 3:
                chains.append(path)
    return chains


def _adjacency(segments, snap_eps: float) -> dict:
    """Snap segment endpoints to a grid and build the undirected adjacency."""

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
    return adj


def point_clusters(pts: np.ndarray, cell: float) -> list[np.ndarray]:
    """8-connected clusters of 2-D points on a grid of `cell`, largest first."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) == 0:
        return []
    keys = np.floor(pts / cell).astype(int)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    index = {tuple(k): i for i, k in enumerate(uniq)}
    parent = list(range(len(uniq)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, k in enumerate(uniq):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                j = index.get((k[0] + dx, k[1] + dy))
                if j is not None:
                    parent[find(i)] = find(j)
    labels = np.array([find(i) for i in range(len(uniq))])[inv.ravel()]
    counts = np.bincount(labels)
    order = np.argsort(-counts)
    return [pts[labels == lab] for lab in order if counts[lab] > 0]


def largest_point_cluster(pts: np.ndarray, cell: float) -> np.ndarray:
    clusters = point_clusters(pts, cell)
    return clusters[0] if clusters else np.asarray(pts, dtype=float)


def polar_section_outline(
    segments: list[tuple[np.ndarray, np.ndarray]],
    axis: np.ndarray,
    r_ref: float,
    *,
    bins: int = 72,
    min_filled_frac: float = 0.6,
    r_tol_frac: float = 0.45,
    r_tol_min: float = 5.0,
) -> np.ndarray | None:
    """Topology-free section outline: median radius of the section points per
    angular bin around `axis`, restricted to points whose radius is near `r_ref`
    (the stoma's radius at the probe height — keeps skin/mat rim points out).
    Missing bins are interpolated. Immune to holes and T-junctions; a stoma is
    star-shaped about its axis so the approximation is faithful. Returns (bins,2)
    points or None when fewer than `min_filled_frac` of bins have data."""
    if not segments:
        return None
    pts = np.array([p for seg in segments for p in seg], dtype=float) - np.asarray(axis, float)
    r = np.hypot(pts[:, 0], pts[:, 1])
    tol = max(r_tol_min, r_tol_frac * r_ref)
    keep = np.abs(r - r_ref) <= tol
    if keep.sum() < 3:
        return None
    r, ang = r[keep], np.arctan2(pts[keep, 1], pts[keep, 0])
    idx = np.floor((ang + math.pi) / (2 * math.pi) * bins).astype(int).clip(0, bins - 1)
    med = np.full(bins, np.nan)
    for b in np.unique(idx):
        med[b] = np.median(r[idx == b])
    filled = np.isfinite(med)
    if filled.sum() < min_filled_frac * bins:
        return None
    # circular interpolation of empty bins
    if not filled.all():
        good = np.flatnonzero(filled)
        ext_x = np.concatenate([good - bins, good, good + bins])
        ext_y = np.concatenate([med[good]] * 3)
        med = np.interp(np.arange(bins), ext_x, ext_y)
    theta = -math.pi + (np.arange(bins) + 0.5) * (2 * math.pi / bins)
    return np.column_stack([med * np.cos(theta), med * np.sin(theta)]) + np.asarray(axis, float)


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
            # the condition guarantees pi.y != pj.y, so the division is safe; clamping
            # a *negative* denominator here (an earlier port bug) broke the test for
            # every downward edge and shifted the polar origin off the centroid.
            x_int = (pj[0] - pi[0]) * (p[1] - pi[1]) / (pj[1] - pi[1]) + pi[0]
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
    loop_xy: np.ndarray | None = None  # raw traced loop (N,2), re-origined like samples

    def plane_xy(self) -> list[tuple[float, float]]:
        return [(s.x, s.y) for s in self.samples]

    def diameter(self) -> float:
        """Base diameter = longest chord of the raw loop (exact); falls back to the
        samples when the loop isn't available (legacy parity results)."""
        if self.loop_xy is not None and len(self.loop_xy) >= 2:
            return max_planar_chord_length(self.loop_xy)
        return max_planar_chord_length(self.samples)


def max_planar_chord_length(samples) -> float:
    """Longest distance between any two points — the 'longest diameter' of the
    outline (BasePerimeterExtractor.maxPlanarChordLength). Accepts a list of
    BasePlaneSample or an (N,2) array. Uses the convex hull so it's exact and fast
    for large loops."""
    if isinstance(samples, np.ndarray):
        pts = np.asarray(samples, dtype=float)
    else:
        pts = np.array([[s.x, s.y] for s in samples], dtype=float)
    if len(pts) < 2:
        return 0.0
    hull = _convex_hull(pts) if len(pts) > 8 else pts
    best = 0.0
    for i in range(len(hull)):
        d = np.linalg.norm(hull[i + 1 :] - hull[i], axis=1)
        if d.size:
            best = max(best, float(d.max()))
    return best


def _convex_hull(pts: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain. The farthest pair of a point set lies on its hull."""
    order = np.lexsort((pts[:, 1], pts[:, 0]))
    p = pts[order]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[np.ndarray] = []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    upper: list[np.ndarray] = []
    for q in p[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return np.array(lower[:-1] + upper[:-1])


def perimeter_from_outline(
    outline: np.ndarray,
    *,
    normal: np.ndarray,
    plane_d: float,
    axis_u: np.ndarray,
    axis_v: np.ndarray,
    floor_h: float,
    max_h: float,
) -> PerimeterResult:
    """Package an (N,2) in-plane outline as a PerimeterResult (same re-origin and
    resampling as the traced path)."""
    loop = np.asarray(outline, dtype=float)
    n = _normalize(np.asarray(normal, dtype=float))
    origin = polar_origin_2d(loop)
    centroid_world = point_on_plane(origin[0], origin[1], plane_d, n, axis_u, axis_v)
    shifted = loop - origin
    span = max(max_h - floor_h, 1e-6)
    return PerimeterResult(
        samples=arc_length_resample(shifted),
        centroid_world=centroid_world,
        axis_u=axis_u,
        axis_v=axis_v,
        plane_normal=n,
        plane_constant=plane_d,
        loop_vertex_count=len(loop),
        slice_offset_fraction=(plane_d - floor_h) / span,
        loop_xy=shifted,
    )


def extract_perimeter(
    vertices: np.ndarray,
    faces: np.ndarray,
    normal: np.ndarray,
    slice_offset_fraction: float,
    *,
    spin_degrees: float = 0.0,
    floor_h: float | None = None,
    max_h: float | None = None,
    plane_h: float | None = None,
    containing: np.ndarray | None = None,
    min_perimeter: float = 0.0,
    return_segments: bool = False,
) -> PerimeterResult:
    """Slice a mesh (vertices (V,3), faces (F,3)) and return the resampled base
    perimeter. `normal` is the slice up-axis. The plane sits at
    `floor_h + fraction·span` (legacy), or at the absolute height `plane_h` along
    the normal when given. `floor_h`/`max_h` default to the vertex height extrema."""
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=int)
    n = _normalize(np.asarray(normal, dtype=float))

    if floor_h is None or max_h is None:
        fh, mh = height_extrema(vertices, n)
        floor_h = fh if floor_h is None else floor_h
        max_h = mh if max_h is None else max_h

    span = max(max_h - floor_h, 1e-6)
    if plane_h is not None:
        plane_d = float(plane_h)
        offset = (plane_d - floor_h) / span
    else:
        offset = max(0.0, min(slice_offset_fraction, 1.0))
        plane_d = floor_h + offset * span
    axis_u, axis_v = slice_basis(n, spin_degrees)
    eps = max(span * 1e-5, 1e-6)

    # Only faces that straddle (or touch) the plane can contribute — on a real
    # 1.3M-face reconstruction that is a few thousand, not all of them. The
    # per-face port below is unchanged; this is a pure prefilter.
    h = vertices @ n - plane_d
    fh = h[faces]
    straddle = (fh.min(axis=1) <= eps) & (fh.max(axis=1) >= -eps)
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    for f in faces[straddle]:
        segments.extend(
            intersect_triangle_plane(
                vertices[f[0]], vertices[f[1]], vertices[f[2]], n, plane_d, axis_u, axis_v, eps
            )
        )
    if not segments:
        raise LoopError("The slice plane did not intersect the mesh.")
    if return_segments:
        return segments  # type: ignore[return-value]

    snap_eps = max(eps * 10, 1e-6, MIN_SNAP)
    if containing is None:
        loop = largest_perimeter_loop(segments, snap_eps)
    else:
        loop = select_loop(
            candidate_loops(segments, snap_eps), containing=containing, min_perimeter=min_perimeter
        )
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
        loop_xy=shifted,
    )
