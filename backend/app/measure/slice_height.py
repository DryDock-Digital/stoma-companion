"""Automatic slice height (P2-4, FR-05).

Given the oriented "up" axis, decide *where* along it to slice the base perimeter —
the legacy app left this a manual `sliceOffsetFraction`. The stoma rises out of the
peristomal skin, so the cross-section area profile along the axis has a broad skin
region that drops sharply to the (smaller, stabilising) stoma at the skin junction.
We find that junction and slice just above it.

Two entry points:
  - `auto_slice_height` — works in **absolute millimetres** between an explicit floor
    (the marker/skin plane height) and ceiling; the margin above the junction is in
    mm. This is what the wired pipeline uses (`measure_scan`).
  - `auto_slice_fraction` — the legacy fraction-of-span form, kept for the P2-1
    diameter board and fixtures whose params are fractions.

All thresholds live in `SliceHeightParams` (configurable per job via `config`), not
in constants: the junction rule is tuned against real geometry at P0-3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import slicing


@dataclass(frozen=True)
class SliceHeightParams:
    n_levels: int = 32
    #: search for the base (the neck) starting this far above the skin junction (mm)
    margin_mm: float = 0.5
    #: … and up to this far above it: the base is the first section in that window
    #: where the profile has stopped changing (the fillet is over), else the narrowest
    neck_window_mm: float = 5.0
    #: |dØ/dh| below this (mm per mm) counts as "stopped changing"
    stable_slope_mm_per_mm: float = 0.35
    #: a downward area step must exceed this fraction of the max area to count as
    #: the skin→stoma junction; otherwise fall back to the widest section
    junction_drop_frac: float = 0.2
    #: ignore this much of the span at either end (fraction) so the caps don't count
    end_trim_frac: float = 0.02
    #: profile only this fraction of the span above the floor — the base is always in
    #: the lower part; the top of a tall stoma is never the slice
    profile_span_frac: float = 0.6


DEFAULT_PARAMS = SliceHeightParams()


def _section(
    vertices, faces, normal, *, plane_h, floor_h, max_h, containing=None, min_perimeter=0.0
):
    try:
        return slicing.extract_perimeter(
            vertices,
            faces,
            normal,
            0.0,
            floor_h=floor_h,
            max_h=max_h,
            plane_h=plane_h,
            containing=containing,
            min_perimeter=min_perimeter,
        )
    except slicing.LoopError:
        return None


def _section_area(vertices, faces, normal, *, plane_h, floor_h, max_h, **kw) -> float:
    result = _section(vertices, faces, normal, plane_h=plane_h, floor_h=floor_h, max_h=max_h, **kw)
    if result is None:
        return 0.0
    pts = result.loop_xy if result.loop_xy is not None else np.array(result.plane_xy())
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _segments(vertices, faces, normal, *, floor_h, max_h, plane_h):
    try:
        return slicing.extract_perimeter(
            vertices,
            faces,
            normal,
            0.0,
            floor_h=floor_h,
            max_h=max_h,
            plane_h=plane_h,
            return_segments=True,
        )
    except slicing.LoopError:
        return []


#: half-thickness of the slab of points that forms a point-cloud section (mm)
CLOUD_BAND_MM = 1.0


def _is_cloud(faces) -> bool:
    return faces is None or np.asarray(faces).size == 0


def section_points(vertices, faces, normal, *, floor_h, max_h, plane_h, band=CLOUD_BAND_MM):
    """(N,2) section points at `plane_h`: triangle/plane crossings for a mesh, or
    the points within ±band of the plane for a point cloud. Empty when nothing."""
    if _is_cloud(faces):
        return slicing.section_points_from_cloud(vertices, normal, plane_h, band)
    segs = _segments(vertices, faces, normal, floor_h=floor_h, max_h=max_h, plane_h=plane_h)
    if not segs:
        return np.zeros((0, 2))
    return np.array([p for s in segs for p in s], dtype=float)


def stoma_axis(
    vertices,
    faces,
    normal,
    *,
    floor_h,
    max_h,
    probe_h: float,
    cell: float = 2.0,
    near=None,
    radius_range=(4.0, 40.0),
    min_points: int = 10,
    band: float = 4 * CLOUD_BAND_MM,
):
    """((u, v) of the stoma axis in slice coordinates, reference radius).

    Section points at `probe_h` are clustered; among clusters that look like a
    stoma section (median radius within `radius_range`, ≥ `min_points`) the one
    **closest to `near`** (the card centre in slice coords) wins — the card is always
    placed next to the stoma, background objects are not. Taking the *largest*
    cluster picked a table edge 100 mm away on the first GPU reconstruction. Without
    `near`, the largest plausible cluster is used. None if nothing is cut."""
    # a thicker slab for the axis probe: it only needs *where*, and a sparse cloud
    # fragments a thin ring into arcs
    pts = section_points(
        vertices, faces, normal, floor_h=floor_h, max_h=max_h, plane_h=probe_h, band=band
    )
    if len(pts) == 0:
        return None
    candidates = []
    for cl in slicing.point_clusters(pts, cell):
        if len(cl) < min_points:
            continue
        axis = cl.mean(axis=0)
        r_ref = float(np.median(np.hypot(*(cl - axis).T)))
        if radius_range[0] <= r_ref <= radius_range[1]:
            candidates.append((axis, r_ref, len(cl)))
    if not candidates:
        return None
    if near is not None:
        near = np.asarray(near, dtype=float)
        candidates.sort(key=lambda c: float(np.linalg.norm(c[0] - near)))
    return candidates[0][0], candidates[0][1]


def polar_diameter_profile(
    vertices,
    faces,
    normal,
    *,
    floor_h,
    max_h,
    axis,
    r_ref,
    params=DEFAULT_PARAMS,
    with_min_width: bool = False,
):
    """(heights_above_floor, Ø or nan[, narrowest width or nan]): the topology-free
    stoma profile. Ø is the longest chord; the narrowest caliper width is what
    actually changes through the skin fillet on elongated stomas (a figure-8's
    length is flat while its waist and lobes still flare), so the base-height rule
    uses it when available."""
    from .shape import caliper_width

    span = max(max_h - floor_h, 1e-6)
    top = floor_h + max(params.profile_span_frac, params.end_trim_frac * 2) * span
    heights = np.linspace(
        floor_h + params.end_trim_frac * span, top - params.end_trim_frac * span, params.n_levels
    )
    out, mins = [], []
    angles = np.deg2rad(np.arange(0, 180, 5.0))
    for h in heights:
        pts = section_points(vertices, faces, normal, floor_h=floor_h, max_h=max_h, plane_h=h)
        est = "mode" if _is_cloud(faces) else "median"
        o = slicing.polar_section_outline(pts, axis, r_ref, estimator=est) if len(pts) else None
        out.append(float("nan") if o is None else slicing.max_planar_chord_length(o))
        if with_min_width:
            mins.append(float("nan") if o is None else min(caliper_width(o, a) for a in angles))
    rel = heights - floor_h
    if with_min_width:
        return rel, np.array(out), np.array(mins)
    return rel, np.array(out)


def diameter_profile(
    vertices, faces, normal, *, floor_h, max_h, containing, min_perimeter, params=DEFAULT_PARAMS
):
    """(heights_above_floor, diameter_mm or nan) at n_levels — the hole-immune stoma
    profile used for the junction rule and reported in diagnostics."""
    span = max(max_h - floor_h, 1e-6)
    heights = np.linspace(
        floor_h + params.end_trim_frac * span, max_h - params.end_trim_frac * span, params.n_levels
    )
    out = []
    for h in heights:
        r = _section(
            vertices,
            faces,
            normal,
            plane_h=h,
            floor_h=floor_h,
            max_h=max_h,
            containing=containing,
            min_perimeter=min_perimeter,
        )
        out.append(float("nan") if r is None else r.diameter())
    return heights - floor_h, np.array(out)


def area_profile_heights(
    vertices, faces, normal, *, floor_h: float, max_h: float, params=DEFAULT_PARAMS
) -> tuple[np.ndarray, np.ndarray]:
    """Cross-section area at `n_levels` heights between floor_h and max_h."""
    span = max(max_h - floor_h, 1e-6)
    lo = floor_h + params.end_trim_frac * span
    hi = max_h - params.end_trim_frac * span
    heights = np.linspace(lo, hi, params.n_levels)
    areas = np.array(
        [
            _section_area(vertices, faces, normal, plane_h=h, floor_h=floor_h, max_h=max_h)
            for h in heights
        ]
    )
    return heights, areas


def area_profile(vertices, faces, normal, n: int = 64, lo: float = 0.02, hi: float = 0.98):
    """Legacy fraction form: cross-section area as a function of slice fraction."""
    floor_h, max_h = slicing.height_extrema(vertices, normal)
    span = max(max_h - floor_h, 1e-6)
    heights = np.linspace(floor_h + lo * span, floor_h + hi * span, n)
    areas = np.array(
        [
            _section_area(vertices, faces, normal, plane_h=h, floor_h=floor_h, max_h=max_h)
            for h in heights
        ]
    )
    return (heights - floor_h) / span, areas


def _junction_height(heights: np.ndarray, areas: np.ndarray, drop_frac: float) -> float | None:
    if areas.max() <= 0:
        return None
    steps = areas[:-1] - areas[1:]
    j = int(np.argmax(steps))
    if steps[j] > drop_frac * areas.max():
        return float(heights[j + 1])
    return None


def base_height_from_profile(
    heights, diameters, params: SliceHeightParams = DEFAULT_PARAMS, min_widths=None
):
    """Base slice height from the profile.

    1. The skin→stoma junction is the largest downward step going up from the floor
       (skin/mat flare → stoma). With no clear step, the lowest valid level.
    2. The base is the **knee** just above it: where the fillet's steep drop ends and
       the stoma body begins, found on the narrowest-width profile when given (the
       longest chord of an elongated stoma barely changes through the fillet) —
       Kneedle-style, the point of maximum distance from the chord joining the ends
       of the [junction + margin, junction + window] segment, on a smoothed profile.
       A fixed "junction + 1 mm" landed on the fillet; "narrowest in the window" walked
       a tapering stoma up to the top of the window.
    """
    diam = np.asarray(diameters, dtype=float)
    dvalid = np.isfinite(diam)
    if not dvalid.any():
        return None
    # junction: on the longest-chord profile (the skin/mat flare shows there)
    hj, dj = heights[dvalid], diam[dvalid]
    steps = dj[:-1] - dj[1:]
    j = int(np.argmax(steps)) if len(steps) else 0
    junction = (
        float(hj[j + 1])
        if len(steps) and steps[j] > params.junction_drop_frac * np.nanmax(dj)
        else float(hj[0])
    )
    # knee: on the narrowest-width profile when available (it keeps changing through
    # the fillet on elongated stomas whose length is already flat)
    prof = diam
    if min_widths is not None:
        mw = np.asarray(min_widths, dtype=float)
        if np.isfinite(mw).sum() >= 4:
            prof = mw
    valid = np.isfinite(prof)
    h, d = heights[valid], prof[valid]
    lo, hi = junction + params.margin_mm, junction + params.neck_window_mm
    window = (h >= lo) & (h <= hi)
    if not window.any():
        return min(lo, float(h[-1]))
    smooth = np.convolve(np.pad(d, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    idx = np.flatnonzero(window)
    if len(idx) < 3:
        return float(h[idx[0]])
    x, y = h[idx], smooth[idx]
    # knee: max perpendicular distance from the chord between the segment's ends,
    # only counted where the profile still falls (a rising profile has no fillet)
    x0, y0, x1, y1 = x[0], y[0], x[-1], y[-1]
    if abs(y0 - y1) < 0.3:  # flat already: the fillet ended before the window
        return float(x[0])
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy) or 1.0
    dist = np.abs(dy * (x - x0) - dx * (y - y0)) / norm
    # the knee is where the curve bows *below* the chord (steep then flat)
    below = (dy * (x - x0) - dx * (y - y0)) * np.sign(dy) < 0
    if not below.any():
        return float(x[0])
    k = int(np.argmax(np.where(below, dist, -1)))
    return float(x[k])


def auto_slice_height(
    vertices,
    faces,
    normal,
    *,
    floor_h: float,
    max_h: float,
    params: SliceHeightParams = DEFAULT_PARAMS,
) -> float:
    """Absolute height (along `normal`) of the stoma base slice.

    Detects the skin→stoma junction as the largest downward step in the area
    profile and returns `junction + margin_mm`. With no clear skin plateau (skin not
    reconstructed, or already cropped away) it uses the widest cross-section — the
    base of the stoma. Raises LoopError if no section exists at all."""
    heights, areas = area_profile_heights(
        vertices, faces, normal, floor_h=floor_h, max_h=max_h, params=params
    )
    if areas.max() <= 0:
        raise slicing.LoopError("No cross-section found between the skin plane and the top.")
    junction = _junction_height(heights, areas, params.junction_drop_frac)
    if junction is not None:
        h = junction + params.margin_mm
    else:
        h = float(heights[int(np.argmax(areas))])
    # never slice above the last non-empty section
    top = float(heights[np.flatnonzero(areas > 0)[-1]])
    return min(max(h, float(heights[0])), top)


def auto_slice_fraction(
    vertices,
    faces,
    normal,
    n: int = 64,
    margin: float = 0.03,
    junction_drop_frac: float = 0.2,
) -> float:
    """Legacy fraction-of-span form of `auto_slice_height` (P2-1 board, fixtures).
    `margin` is a fraction of the span here, for backwards compatibility."""
    floor_h, max_h = slicing.height_extrema(vertices, normal)
    span = max(max_h - floor_h, 1e-6)
    params = SliceHeightParams(
        n_levels=n, margin_mm=margin * span, junction_drop_frac=junction_drop_frac
    )
    try:
        h = auto_slice_height(vertices, faces, normal, floor_h=floor_h, max_h=max_h, params=params)
    except slicing.LoopError:
        return 0.5
    return float(min(max((h - floor_h) / span, 0.0), 1.0))


def base_diameter(
    vertices,
    faces,
    normal,
    scale: float = 1.0,
    *,
    auto: bool = True,
    fraction: float = 0.5,
) -> float:
    """Base diameter (mm) = longest planar chord of the base slice × scale. Uses the
    auto-detected height (`auto`) or a manual `fraction`. Shared by the diameter board
    and the keyframe-sweep rig (P2-5)."""
    frac = auto_slice_fraction(vertices, faces, normal) if auto else fraction
    result = slicing.extract_perimeter(vertices, faces, normal, frac)
    return result.diameter() * scale
