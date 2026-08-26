"""End-to-end measurement of a reconstruction (P1-10).

Given a reconstructed mesh, the COLMAP camera poses, and the keyframes, produce the
patient-facing result the web app renders: base diameter (mm), the base outline + the
grace-ring wafer outline, deviation vs caliper truth, and the G-code.

Real-world scale + "up" come from the ArUco marker: detect it in the keyframes,
triangulate its 4 corners across views using the COLMAP poses (the same machinery as
orientation, P2-2), then scale = marker_side_mm / measured_side (P1-6) and the marker
plane normal is the slice axis (FR-04). The OpenMVS mesh shares COLMAP's world frame,
so the scale + normal apply to it directly. Everything downstream is the ported maths
(slice → diameter → outline → G-code).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import gcode as gcode_mod
from . import outline as outline_mod
from . import slicing
from .aruco import detect_markers, scale_from_marker_corners
from .orientation import PinholeCamera, recover_marker_plane
from .slice_height import auto_slice_fraction


class MeasureError(RuntimeError):
    pass


@dataclass
class MeasureResult:
    diameter_mm: float
    outline_mm: list[tuple[float, float]]
    wafer_outline_mm: list[tuple[float, float]]
    scale_mm_per_unit: float
    marker_views: int
    tolerance_mm: float
    grace_ring_mm: float
    truth_mm: float | None = None
    deviation_mm: float | None = None
    within_tolerance: bool | None = None
    engine: str | None = None
    gcode: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def result_json(self) -> dict:
        """The `result` blob stored on the job + rendered by the web app."""
        return {
            "diameter_mm": round(self.diameter_mm, 3),
            "deviation_mm": None if self.deviation_mm is None else round(self.deviation_mm, 3),
            "tolerance_mm": self.tolerance_mm,
            "within_tolerance": self.within_tolerance,
            "outline_mm": [[round(x, 3), round(y, 3)] for x, y in self.outline_mm],
            "wafer_outline_mm": [[round(x, 3), round(y, 3)] for x, y in self.wafer_outline_mm],
            "engine": self.engine,
            "scale_mm_per_unit": round(self.scale_mm_per_unit, 6),
        }


def measure_scan(
    vertices: np.ndarray,
    faces: np.ndarray,
    cameras: dict[str, PinholeCamera],
    keyframes: dict[str, np.ndarray],
    *,
    marker_side_mm: float,
    marker_id: int | None = None,
    grace_ring_mm: float = 3.0,
    tolerance_mm: float = 1.0,
    truth_mm: float | None = None,
    engine: str | None = None,
) -> MeasureResult:
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=int)

    # 1. detect the marker in every keyframe that has a pose; collect corner obs.
    cams: list[PinholeCamera] = []
    obs: list[np.ndarray] = []
    for name, cam in cameras.items():
        img = keyframes.get(name)
        if img is None:
            continue
        dets = detect_markers(img)
        if marker_id is not None:
            m = next((d for d in dets if d.marker_id == marker_id), None)
        else:
            m = dets[0] if dets else None
        if m is not None:
            cams.append(cam)
            obs.append(m.corners_px)
    if len(cams) < 2:
        raise MeasureError(f"marker seen in only {len(cams)} view(s); need >= 2 for scale")

    # 2. triangulate the marker → scale (mm/unit) + slice "up" axis.
    plane = recover_marker_plane(cams, obs)
    scale_res = scale_from_marker_corners(plane.corners, marker_side_mm)
    scale = scale_res.scale_mm_per_scene_unit
    normal = plane.normal

    # 3. mesh → mm, slice the base, measure diameter + outline.
    vertices_mm = vertices * scale
    fraction = auto_slice_fraction(vertices_mm, faces, normal)
    perimeter = slicing.extract_perimeter(vertices_mm, faces, normal, fraction)
    diameter_mm = slicing.max_planar_chord_length(perimeter.samples)

    base_outline = perimeter.plane_xy()
    ideal = outline_mod.generate(
        base_outline, clearance_mm=grace_ring_mm, tolerance_mm=tolerance_mm
    )
    wafer_outline = ideal.points if ideal else base_outline
    gcode_text = gcode_mod.perimeter_gcode(perimeter, units_mm=True)

    deviation = None if truth_mm is None else diameter_mm - truth_mm
    within = None if deviation is None else abs(deviation) <= tolerance_mm

    return MeasureResult(
        diameter_mm=diameter_mm,
        outline_mm=[(float(x), float(y)) for x, y in base_outline],
        wafer_outline_mm=[(float(x), float(y)) for x, y in wafer_outline],
        scale_mm_per_unit=scale,
        marker_views=len(cams),
        tolerance_mm=tolerance_mm,
        grace_ring_mm=grace_ring_mm,
        truth_mm=truth_mm,
        deviation_mm=deviation,
        within_tolerance=within,
        engine=engine,
        gcode=gcode_text,
        extra={"marker_side_cv": scale_res.side_cv, "slice_fraction": fraction},
    )
