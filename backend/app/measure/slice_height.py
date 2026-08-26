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

from dataclasses import dataclass

import numpy as np

from . import slicing


@dataclass(frozen=True)
class SliceHeightParams:
    n_levels: int = 64
    #: slice this far above the detected skin junction (mm)
    margin_mm: float = 1.0
    #: a downward area step must exceed this fraction of the max area to count as
    #: the skin→stoma junction; otherwise fall back to the widest section
    junction_drop_frac: float = 0.2
    #: ignore this much of the span at either end (fraction) so the caps don't count
    end_trim_frac: float = 0.02


DEFAULT_PARAMS = SliceHeightParams()


def _section_area(vertices, faces, normal, *, plane_h, floor_h, max_h) -> float:
    try:
        result = slicing.extract_perimeter(
            vertices, faces, normal, 0.0, floor_h=floor_h, max_h=max_h, plane_h=plane_h
        )
    except slicing.LoopError:
        return 0.0
    pts = result.loop_xy if result.loop_xy is not None else np.array(result.plane_xy())
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


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
