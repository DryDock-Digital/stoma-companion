"""End-to-end measurement of a reconstruction (P1-10).

Given a reconstructed mesh, the camera poses of its keyframes (engine-neutral, see
`poses.py`), and the keyframe images, produce the patient-facing result the web app
renders — base diameter (mm), base outline, grace-ring wafer outline, deviation vs
caliper truth — plus the wafer G-code.

Chain (every step parameterised through `MeasureParams`, carried on the job config):

  1. detect the ArUco marker in every posed keyframe; weight views by marker size
  2. triangulate its corners robustly (undistorted, outlier views dropped) →
     scale = marker_side_mm / side (P1-6) and the marker-plane normal (P2-2)
  3. mesh → mm; crop to a region of interest around the marker (drops table /
     background so heights and loops are about the stoma, not the scene)
  4. refine "up" with a RANSAC fit of the peristomal skin near the marker (P2-3, D16)
  5. floor = marker/skin plane height; auto slice height above the skin junction (P2-4)
  6. slice → base loop → exact diameter (max chord over the loop vertices)
  7. FR-07 grace-ring wafer outline (true polygon offset) → G-code in the chosen dialect
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..errors import StageError
from . import gcode as gcode_mod
from . import outline as outline_mod
from . import shape as shape_mod
from . import slicing
from .aruco import ARUCO_DICT, detect_markers, quad_area_px, scale_from_marker_corners
from .orientation import PinholeCamera, recover_marker_plane, refine_up_axis_with_skin
from .slice_height import (
    SliceHeightParams,
    base_height_from_profile,
    polar_diameter_profile,
    stoma_axis,
)


class MeasureError(StageError):
    stage = "measure"


class MarkerNotFound(MeasureError):
    stage = "marker"


@dataclass(frozen=True)
class MeasureParams:
    """Every knob of the measurement chain. Defaults are the demo configuration;
    a job's `config` dict overrides any of them (`MeasureParams.from_config`)."""

    marker_side_mm: float = 50.0
    marker_id: int | None = None
    aruco_dict: str = ARUCO_DICT
    grace_ring_mm: float = outline_mod.DEFAULT_CLEARANCE_MM  # FR-07 — parameter, not constant
    tolerance_mm: float = outline_mod.DEFAULT_TOLERANCE_MM  # FR-09
    truth_mm: float | None = None
    gcode_dialect: str = "grbl"
    # region of interest around the marker centre (mm): the stoma is always within
    # a hand's width of the card; everything further is table/background.
    roi_radius_mm: float = 120.0
    roi_below_mm: float = 10.0
    roi_above_mm: float = 60.0
    #: height above the skin plane used to locate the stoma axis (mid-stoma)
    axis_probe_mm: float = 10.0
    #: loops shorter than this are noise, never the stoma section (mm)
    min_section_perimeter_mm: float = 30.0
    #: plausible stoma section radius at the axis probe height (mm); clusters outside
    #: this are background, never the stoma
    stoma_radius_min_mm: float = 4.0
    stoma_radius_max_mm: float = 40.0
    # peristomal-skin band used for the RANSAC "up" refinement (mm from marker plane)
    skin_band_mm: float = 4.0
    skin_ransac_threshold_mm: float = 1.5
    skin_max_deviation_deg: float = 15.0
    # marker triangulation
    reproj_threshold_px: float = 2.0
    min_marker_views: int = 2
    slice: SliceHeightParams = field(default_factory=SliceHeightParams)

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None, **overrides: Any) -> MeasureParams:
        cfg = dict(cfg or {})
        cfg.update({k: v for k, v in overrides.items() if v is not None})
        slice_cfg = {k[len("slice_") :]: v for k, v in cfg.items() if k.startswith("slice_")}
        known = {f for f in cls.__dataclass_fields__ if f != "slice"}
        kwargs = {k: v for k, v in cfg.items() if k in known}
        for key in ("marker_side_mm", "grace_ring_mm", "tolerance_mm", "roi_radius_mm"):
            if key in kwargs:
                kwargs[key] = float(kwargs[key])
        if kwargs.get("truth_mm") is not None:
            kwargs["truth_mm"] = float(kwargs["truth_mm"])
        slice_known = set(SliceHeightParams.__dataclass_fields__)
        slice_kwargs = {k: v for k, v in slice_cfg.items() if k in slice_known}
        return cls(slice=SliceHeightParams(**slice_kwargs), **kwargs)

    def to_config(self) -> dict[str, Any]:
        d = asdict(self)
        sl = d.pop("slice")
        d.update({f"slice_{k}": v for k, v in sl.items()})
        return d


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
    gcode_dialect: str = ""
    orientation_method: str = ""
    clearance: dict[str, float] | None = None
    shape: dict[str, Any] | None = None  # directional widths of the base outline
    wafer_shape: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def result_json(self) -> dict:
        """The `result` blob stored on the job + rendered by the web app. The G-code
        itself is *not* here — it's stored as an object (`paths.gcode_key`) and
        referenced by `gcode_path`, added by the stage that uploads it."""
        return {
            "diameter_mm": round(float(self.diameter_mm), 3),
            "deviation_mm": None
            if self.deviation_mm is None
            else round(float(self.deviation_mm), 3),
            "tolerance_mm": float(self.tolerance_mm),
            "within_tolerance": self.within_tolerance,
            "grace_ring_mm": float(self.grace_ring_mm),
            "outline_mm": [[round(float(x), 3), round(float(y), 3)] for x, y in self.outline_mm],
            "wafer_outline_mm": [
                [round(float(x), 3), round(float(y), 3)] for x, y in self.wafer_outline_mm
            ],
            "engine": self.engine,
            "scale_mm_per_unit": round(float(self.scale_mm_per_unit), 6),
            "marker_views": int(self.marker_views),
            "orientation_method": self.orientation_method,
            "gcode_dialect": self.gcode_dialect,
            "clearance_mm": self.clearance,
            "shape": self.shape,
            "wafer_shape": self.wafer_shape,
            "diagnostics": _jsonable(self.extra),
        }


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if isinstance(v, np.generic):
            v = v.item()
        elif isinstance(v, np.ndarray):
            v = v.tolist()
        out[k] = v
    return out


def measure_scan(
    vertices: np.ndarray,
    faces: np.ndarray,
    cameras: dict[str, PinholeCamera],
    keyframes: dict[str, np.ndarray],
    *,
    params: MeasureParams | None = None,
    engine: str | None = None,
    # convenience overrides (tests / CLI); `params` wins when both are given
    marker_side_mm: float | None = None,
    marker_id: int | None = None,
    grace_ring_mm: float | None = None,
    tolerance_mm: float | None = None,
    truth_mm: float | None = None,
) -> MeasureResult:
    if params is None:
        params = MeasureParams.from_config(
            {},
            marker_side_mm=marker_side_mm,
            marker_id=marker_id,
            grace_ring_mm=grace_ring_mm,
            tolerance_mm=tolerance_mm,
            truth_mm=truth_mm,
        )
    vertices = np.asarray(vertices, dtype=float)
    faces = (
        np.asarray(faces, dtype=int).reshape(-1, 3)
        if np.asarray(faces).size
        else np.zeros((0, 3), int)
    )
    is_cloud = len(faces) == 0  # dense point cloud (no meshing step) — polar outlines only
    if len(vertices) == 0:
        raise MeasureError("empty reconstruction", user_message=None)

    # 1. detect the marker in every keyframe that has a pose; collect corner obs.
    cams: list[PinholeCamera] = []
    obs: list[np.ndarray] = []
    weights: list[float] = []
    for name, cam in cameras.items():
        img = keyframes.get(name)
        if img is None:
            continue
        dets = detect_markers(img, params.aruco_dict)
        if params.marker_id is not None:
            m = next((d for d in dets if d.marker_id == params.marker_id), None)
        else:
            m = dets[0] if dets else None
        if m is not None:
            cams.append(cam)
            obs.append(m.corners_px)
            weights.append(float(np.sqrt(max(quad_area_px(m.corners_px), 1.0))))
    if len(cams) < params.min_marker_views:
        raise MarkerNotFound(
            f"marker seen in only {len(cams)} posed view(s) of {len(cameras)}; "
            f"need >= {params.min_marker_views} for scale"
        )
    w = np.asarray(weights) / max(np.mean(weights), 1e-9)

    # 2. triangulate the marker → scale (mm/unit) + marker-plane "up".
    plane = recover_marker_plane(
        cams, obs, weights=w, reproj_threshold_px=params.reproj_threshold_px
    )
    try:
        scale_res = scale_from_marker_corners(plane.corners, params.marker_side_mm)
    except Exception as exc:  # InconsistentSquareError → patient-safe marker message
        raise MarkerNotFound(str(exc)) from exc
    scale = scale_res.scale_mm_per_scene_unit
    marker_normal = plane.normal
    marker_centre_mm = plane.centroid * scale
    cameras_toward = np.mean([c.center for c in cams], axis=0) * scale

    # 3. mesh → mm, crop to the region of interest around the marker.
    vertices_mm = vertices * scale
    try:
        roi_v, roi_f, inside = slicing.crop_mesh(
            vertices_mm,
            faces,
            marker_centre_mm,
            marker_normal,
            radius=params.roi_radius_mm,
            below=params.roi_below_mm,
            above=params.roi_above_mm,
        )
    except slicing.LoopError as exc:
        raise MeasureError(str(exc)) from exc

    # 4. refine "up" with the peristomal skin around the marker (D16).
    h_marker = (roi_v - marker_centre_mm) @ marker_normal
    skin_pts = roi_v[inside & (np.abs(h_marker) <= params.skin_band_mm)]
    choice = refine_up_axis_with_skin(
        marker_normal,
        marker_centre_mm,
        skin_pts,
        orient_toward=cameras_toward,
        threshold_mm=params.skin_ransac_threshold_mm,
        max_deviation_deg=params.skin_max_deviation_deg,
    )
    normal = choice.normal

    # 5. floor = skin plane height; ceiling = top of the ROI; auto slice height.
    floor_h = float(marker_centre_mm @ normal)
    heights = roi_v[inside] @ normal
    max_h = float(heights.max())
    if is_cloud and len(roi_v) < 500:
        raise MeasureError("too few reconstructed points around the card")
    if max_h - floor_h < 1.0:
        raise MeasureError("nothing rises above the skin plane inside the region of interest")
    probe = min(floor_h + params.axis_probe_mm, floor_h + 0.6 * (max_h - floor_h))
    au, av = slicing.slice_basis(normal)
    card_uv = np.array([float(marker_centre_mm @ au), float(marker_centre_mm @ av)])
    found = stoma_axis(
        roi_v,
        roi_f,
        normal,
        floor_h=floor_h,
        max_h=max_h,
        probe_h=probe,
        near=card_uv,
        radius_range=(params.stoma_radius_min_mm, params.stoma_radius_max_mm),
    )
    if found is None:
        raise MeasureError("no stoma section found above the skin plane")
    axis, r_ref = found
    prof_h, prof_d = polar_diameter_profile(
        roi_v,
        roi_f,
        normal,
        floor_h=floor_h,
        max_h=max_h,
        axis=axis,
        r_ref=r_ref,
        params=params.slice,
    )
    rel_h = base_height_from_profile(prof_h, prof_d, params.slice)
    if rel_h is None:
        raise MeasureError("no stoma section at any height above the skin plane")
    plane_h = floor_h + rel_h

    # 6. base outline at that height: the traced loop around the stoma axis when
    # the mesh topology allows it, else the polar outline (holes / T-junctions).
    # The traced loop must agree with the polar Ø within 10% or it's a merged loop.
    polar_d = float(np.interp(rel_h, prof_h, np.nan_to_num(prof_d, nan=0.0)))
    outline_method = "loop"
    try:
        if is_cloud:
            raise slicing.LoopError("point cloud: no traced loops")
        perimeter = slicing.extract_perimeter(
            roi_v,
            roi_f,
            normal,
            0.0,
            floor_h=floor_h,
            max_h=max_h,
            plane_h=plane_h,
            containing=axis,
            min_perimeter=params.min_section_perimeter_mm,
        )
        if polar_d > 0 and abs(perimeter.diameter() - polar_d) > 0.10 * polar_d:
            raise slicing.LoopError("traced loop disagrees with the polar profile")
    except slicing.LoopError:
        outline_method = "polar-cloud" if is_cloud else "polar"
        from .slice_height import section_points

        pts = section_points(roi_v, roi_f, normal, floor_h=floor_h, max_h=max_h, plane_h=plane_h)
        outline = slicing.polar_section_outline(
            pts, axis, r_ref, estimator="mode" if is_cloud else "median"
        )
        if outline is None:
            raise MeasureError("no stoma section at the base height") from None
        au, av = slicing.slice_basis(normal)
        perimeter = slicing.perimeter_from_outline(
            outline,
            normal=normal,
            plane_d=plane_h,
            axis_u=au,
            axis_v=av,
            floor_h=floor_h,
            max_h=max_h,
        )
    diameter_mm = perimeter.diameter()

    # 7. FR-07 wafer outline + G-code
    base_outline = perimeter.plane_xy()
    ideal = outline_mod.generate(
        base_outline, clearance_mm=params.grace_ring_mm, tolerance_mm=params.tolerance_mm
    )
    if ideal is None:
        raise MeasureError("could not offset the base outline")
    wafer_outline = ideal.points
    dialect = gcode_mod.get_dialect(params.gcode_dialect)
    gcode_text = gcode_mod.ideal_fit_gcode(
        wafer_outline, perimeter, clearance_mm=params.grace_ring_mm, dialect=dialect
    )

    base_shape = shape_mod.profile(base_outline)
    wafer_shape = shape_mod.profile(wafer_outline)
    if base_shape is not None:
        diameter_mm = base_shape.max_width_mm  # widest caliper span == longest chord

    deviation = None if params.truth_mm is None else diameter_mm - params.truth_mm
    within = None if deviation is None else abs(deviation) <= params.tolerance_mm

    return MeasureResult(
        diameter_mm=diameter_mm,
        outline_mm=[(float(x), float(y)) for x, y in base_outline],
        wafer_outline_mm=[(float(x), float(y)) for x, y in wafer_outline],
        scale_mm_per_unit=scale,
        marker_views=plane.views_used,
        tolerance_mm=params.tolerance_mm,
        grace_ring_mm=params.grace_ring_mm,
        truth_mm=params.truth_mm,
        deviation_mm=deviation,
        within_tolerance=within,
        engine=engine,
        gcode=gcode_text,
        gcode_dialect=dialect.name,
        orientation_method=choice.method,
        shape=None if base_shape is None else base_shape.to_json(),
        wafer_shape=None if wafer_shape is None else wafer_shape.to_json(),
        clearance={
            "min": round(ideal.clearance.min, 3),
            "mean": round(ideal.clearance.mean, 3),
            "max": round(ideal.clearance.max, 3),
            "passes": ideal.clearance.passes,
        },
        extra={
            "marker_side_cv": scale_res.side_cv,
            "marker_views_total": plane.views_total,
            "marker_reprojection_px": plane.reprojection_px,
            "marker_planarity_rms": plane.rms_planarity,
            "skin_points": int(len(skin_pts)),
            "skin_angle_to_marker_deg": choice.angle_to_marker_deg,
            "skin_inlier_fraction": choice.skin_inlier_fraction,
            "slice_height_mm_above_skin": plane_h - floor_h,
            "stoma_axis_uv": [float(axis[0]), float(axis[1])],
            "card_uv": [float(card_uv[0]), float(card_uv[1])],
            "axis_to_card_mm": float(np.linalg.norm(axis - card_uv)),
            "stoma_probe_radius_mm": r_ref,
            "outline_method": outline_method,
            "polar_diameter_at_base_mm": polar_d,
            "diameter_profile": [
                [round(float(h), 2), None if not np.isfinite(dd) else round(float(dd), 2)]
                for h, dd in zip(prof_h, prof_d, strict=True)
            ],
            "roi_vertices": int(len(roi_v)),
            "input_kind": "point-cloud" if is_cloud else "mesh",
            "loop_vertices": perimeter.loop_vertex_count,
        },
    )
