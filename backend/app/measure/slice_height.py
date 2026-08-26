"""Automatic slice height (P2-4, FR-05).

Given the oriented "up" axis, decide *where* along it to slice the base perimeter —
the legacy app left this a manual `sliceOffsetFraction`. The stoma rises out of the
peristomal skin, so the cross-section area profile along the axis has a broad skin
region that drops sharply to the (smaller, stabilising) stoma at the skin junction.
We find that junction and slice just above it.

Pure geometry over the ported slicer (`slicing.extract_perimeter`), so it's scored
on the P2-1 diameter board via `AutoHeightDiameterMethod`. Real-fixture tuning of the
junction rule — and Cole's FR-10 reference point — are deferred (P0-3).
"""

from __future__ import annotations

import numpy as np

from . import slicing


def _section_area(vertices, faces, normal, fraction, floor_h, max_h) -> float:
    try:
        result = slicing.extract_perimeter(
            vertices, faces, normal, fraction, floor_h=floor_h, max_h=max_h
        )
    except slicing.LoopError:
        return 0.0
    pts = np.array([[s.x, s.y] for s in result.samples])
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def area_profile(
    vertices: np.ndarray,
    faces: np.ndarray,
    normal,
    n: int = 64,
    lo: float = 0.02,
    hi: float = 0.98,
) -> tuple[np.ndarray, np.ndarray]:
    """Cross-section area as a function of slice fraction along `normal`."""
    floor_h, max_h = slicing.height_extrema(vertices, normal)
    fractions = np.linspace(lo, hi, n)
    areas = np.array([_section_area(vertices, faces, normal, f, floor_h, max_h) for f in fractions])
    return fractions, areas


def auto_slice_fraction(
    vertices: np.ndarray,
    faces: np.ndarray,
    normal,
    n: int = 64,
    margin: float = 0.03,
    junction_drop_frac: float = 0.2,
) -> float:
    """Slice fraction at the stoma base.

    Detects the skin→stoma junction as the largest downward step in the area
    profile and returns a fraction just above it (into the stabilised stoma
    cross-section). If there is no clear skin plateau (e.g. the skin wasn't
    reconstructed), falls back to the widest cross-section — the base of the stoma.
    """
    fractions, areas = area_profile(vertices, faces, normal, n)
    if areas.max() <= 0:
        return 0.5

    steps = areas[:-1] - areas[1:]
    j = int(np.argmax(steps))
    if steps[j] > junction_drop_frac * areas.max():
        base = fractions[j + 1] + margin
    else:
        base = fractions[int(np.argmax(areas))]
    return float(min(max(base, 0.0), 1.0))


def base_diameter(
    vertices: np.ndarray,
    faces: np.ndarray,
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
    return slicing.max_planar_chord_length(result.samples) * scale
