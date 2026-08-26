"""G-code generation (P1-8).

Two exporters, ported from the legacy Mac app:
  - `perimeter_gcode` / `ideal_fit_gcode`  ← CompanionMac/BasePerimeterExport.swift
    A continuous G1 chain around the closed outline (the primary cut path).
  - `PolarPathExport` (build + polar_file_text)  ← CompanionMac/PolarPathExport.swift
    Fixed-ω polar plan for the platter plotter (M200/M201/M202 custom format).

`user_scale` and the m→mm multiplier mirror the Swift so G-code geometry matches
the golden fixtures exactly (parity target).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .slicing import SAMPLE_COUNT, PerimeterResult

Point = tuple[float, float]

GCODE_FEED_MM_PER_MIN = 1200


def _f6(v: float) -> str:
    return f"{v:.6f}"


def spatial_multiplier(units_mm: bool, user_scale: float) -> float:
    return (1000.0 if units_mm else 1.0) * user_scale


def scaled_gcode_plane_xy(
    result: PerimeterResult, units_mm: bool, user_scale: float
) -> list[Point]:
    mul = spatial_multiplier(units_mm, user_scale)
    return [(s.x * mul, s.y * mul) for s in result.samples]


def _gcode_block_from_ring(
    ring: list[Point],
    result: PerimeterResult,
    units_mm: bool,
    user_scale: float,
    header_extra: str | None,
) -> str:
    if not ring:
        return "; Empty perimeter\n"

    mul = spatial_multiplier(units_mm, user_scale)
    c = result.centroid_world * mul
    n = result.plane_normal
    au, av = result.axis_u, result.axis_v
    first = ring[0]

    lines: list[str] = ["; Base perimeter — Stoma Companion"]
    if header_extra:
        lines.append(header_extra)
    lines.append(
        "; Vertices in slice plane: X along axisU, Y along axisV (not machine homing / WCS)."
    )
    lines.append(f"; polar_origin_world = ({_f6(c[0])}, {_f6(c[1])}, {_f6(c[2])})")
    lines.append(f"; planeNormal = ({_f6(n[0])}, {_f6(n[1])}, {_f6(n[2])})")
    lines.append(f"; axisU = ({_f6(au[0])}, {_f6(au[1])}, {_f6(au[2])})")
    lines.append(f"; axisV = ({_f6(av[0])}, {_f6(av[1])}, {_f6(av[2])})")
    lines.append(
        f"; samples = {SAMPLE_COUNT} loopVertices = {result.loop_vertex_count} "
        f"sliceOffsetFraction = {_f6(result.slice_offset_fraction)}"
    )
    lines.append(
        "; path: equal arc-length spacing along closed slice outline (continuous G1 chain)"
    )
    lines.append(f"; user_linear_scale = {user_scale} combined_spatial_multiplier = {mul}")
    if units_mm:
        lines.append(f"; Units: millimeters (G21). G1 feed F{GCODE_FEED_MM_PER_MIN} mm/min.")
    else:
        lines.append(
            "; Units: meters (no G21). G1 moves omit F — set feed on your controller if needed."
        )
    lines.append("; StomaPlotter: G28 homes to limit switch then moves carriage to center (0,0).")
    lines.append("")

    lines.append("G28")
    if units_mm:
        lines.append("G21")
    lines.append("G90")
    lines.append("; Lead-in: StomaPlotter arcs from center to first point (no G0 to P1).")

    feed_suffix = f" F{GCODE_FEED_MM_PER_MIN}" if units_mm else ""
    lines.append(f"G1 X{_f6(first[0])} Y{_f6(first[1])} Z0.000000{feed_suffix}")
    for p in ring[1:]:
        lines.append(f"G1 X{_f6(p[0])} Y{_f6(p[1])} Z0.000000{feed_suffix}")
    lines.append(f"G1 X{_f6(first[0])} Y{_f6(first[1])} Z0.000000{feed_suffix}")
    lines.append("M2")
    return "\n".join(lines) + "\n"


def perimeter_gcode(result: PerimeterResult, units_mm: bool = True, user_scale: float = 1.0) -> str:
    """Primary base-perimeter G-code (BasePerimeterExport.gcodeBlock)."""
    ring = scaled_gcode_plane_xy(result, units_mm, user_scale)
    return _gcode_block_from_ring(ring, result, units_mm, user_scale, header_extra=None)


def ideal_fit_gcode(
    ideal_ring: list[Point], result: PerimeterResult, units_mm: bool = True, user_scale: float = 1.0
) -> str:
    """Wafer-cut (Ideal-Fit) G-code — same wrapper, pre-offset ring (in export units)."""
    return _gcode_block_from_ring(
        ideal_ring,
        result,
        units_mm,
        user_scale,
        header_extra="; Outline: Ideal Fit (wafer cut, outward clearance from primary)",
    )


# --- Polar path plan (PolarPathExport) -------------------------------------


@dataclass
class PolarSegment:
    r0_mm: float
    r1_mm: float
    d_theta_rad: float


@dataclass
class PolarValidation:
    max_chord_error_mm: float
    mean_chord_error_mm: float
    max_radial_speed_mm_s: float
    winding_rad: float
    min_radius_mm: float
    passes_radius_min: bool
    passes_radial_speed: bool
    passes_winding: bool


@dataclass
class PolarPathPlan:
    plane_xy: list[Point]
    radii_mm: list[float]
    phi_unwrapped_rad: list[float]
    segments: list[PolarSegment]
    segment_radial_speed_mm_s: list[float]
    start_phi_rad: float
    start_r_mm: float
    rpm: float
    center_offset_mm: float
    rotation_offset_rad: float
    validation: PolarValidation


class PolarPathExport:
    FORMAT_VERSION = "stoma_polar_v1"
    DEFAULT_RPM = 3.0
    CENTER_FROM_HOME_MM = 38.0
    MIN_RADIUS_MM = 2.0
    DEFAULT_MAX_RADIAL_SPEED_MM_S = 8.0

    @staticmethod
    def _signed_area(poly: list[Point]) -> float:
        n = len(poly)
        if n < 3:
            return 0.0
        a = 0.0
        for i in range(n):
            j = (i + 1) % n
            a += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1]
        return a * 0.5

    @classmethod
    def _ensure_ccw(cls, poly: list[Point]) -> list[Point]:
        if len(poly) < 3 or cls._signed_area(poly) >= 0:
            return poly
        return [poly[0]] + list(reversed(poly[1:]))

    @classmethod
    def build(
        cls,
        plane_xy: list[Point],
        rpm: float | None = None,
        max_radial_speed_mm_s: float | None = None,
        samples_per_segment: int = 8,
    ) -> PolarPathPlan | None:
        if len(plane_xy) < 3:
            return None
        rpm = cls.DEFAULT_RPM if rpm is None else rpm
        max_v_gate = (
            cls.DEFAULT_MAX_RADIAL_SPEED_MM_S
            if max_radial_speed_mm_s is None
            else max_radial_speed_mm_s
        )
        n = len(plane_xy)

        phi0 = math.atan2(plane_xy[0][1], plane_xy[0][0])
        c0, s0 = math.cos(-phi0), math.sin(-phi0)
        rotated = [(p[0] * c0 - p[1] * s0, p[0] * s0 + p[1] * c0) for p in plane_xy]
        rotated = cls._ensure_ccw(rotated)

        radii = [math.hypot(p[0], p[1]) for p in rotated]
        phi = [math.atan2(p[1], p[0]) for p in rotated]
        for i in range(1, n):
            d = phi[i] - phi[i - 1]
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            phi[i] = phi[i - 1] + d

        segments: list[PolarSegment] = []
        speeds: list[float] = []
        omega = rpm * 2 * math.pi / 60
        for i in range(n):
            r0 = radii[i]
            r1 = radii[(i + 1) % n]
            d_theta = phi[i + 1] - phi[i] if i < n - 1 else (2 * math.pi - phi[n - 1])
            segments.append(PolarSegment(r0, r1, d_theta))
            speeds.append(abs(r1 - r0) * omega / abs(d_theta) if abs(d_theta) > 1e-9 else 0.0)

        winding = sum(s.d_theta_rad for s in segments)
        min_r = min(radii) if radii else 0.0
        max_v = max(speeds) if speeds else 0.0
        chord_max, chord_mean = cls._chord_error(rotated, phi, segments, samples_per_segment)

        validation = PolarValidation(
            max_chord_error_mm=chord_max,
            mean_chord_error_mm=chord_mean,
            max_radial_speed_mm_s=max_v,
            winding_rad=winding,
            min_radius_mm=min_r,
            passes_radius_min=min_r >= cls.MIN_RADIUS_MM,
            passes_radial_speed=max_v <= max_v_gate,
            passes_winding=abs(winding - 2 * math.pi) < 0.15
            and all(0 < s.d_theta_rad <= math.pi for s in segments),
        )
        return PolarPathPlan(
            plane_xy=plane_xy,
            radii_mm=radii,
            phi_unwrapped_rad=phi,
            segments=segments,
            segment_radial_speed_mm_s=speeds,
            start_phi_rad=0.0,
            start_r_mm=radii[0],
            rpm=rpm,
            center_offset_mm=cls.CENTER_FROM_HOME_MM,
            rotation_offset_rad=phi0,
            validation=validation,
        )

    @classmethod
    def _chord_error(cls, plane_xy, phi, segments, samples_per_segment):
        n = len(plane_xy)
        max_err = sum_err = 0.0
        count = 0
        for i in range(n):
            a = plane_xy[i]
            b = plane_xy[(i + 1) % n]
            seg = segments[i]
            theta0 = phi[i]
            for j in range(1, samples_per_segment + 1):
                t = j / samples_per_segment
                th = theta0 + seg.d_theta_rad * t
                r = seg.r0_mm + (seg.r1_mm - seg.r0_mm) * t
                err = _dist_to_segment(r * math.cos(th), r * math.sin(th), a[0], a[1], b[0], b[1])
                max_err = max(max_err, err)
                sum_err += err
                count += 1
        return max_err, (sum_err / count if count else 0.0)

    @classmethod
    def polar_file_text(cls, plan: PolarPathPlan) -> str:
        v = plan.validation
        lines = [
            f";{cls.FORMAT_VERSION} RPM={_f6(plan.rpm)} COUNT={len(plan.segments)}",
            f"; CENTER_MM={_f6(plan.center_offset_mm)} WINDING_RAD={_f6(v.winding_rad)}",
            f"; MAX_CHORD_ERR_MM={_f6(v.max_chord_error_mm)} MAX_VR_MM_S={_f6(v.max_radial_speed_mm_s)}",  # noqa: E501
            f"; START_PHI_RAD=0 START_R_MM={_f6(plan.start_r_mm)} "
            f"ROTATED_ORIG_PHI0={_f6(plan.phi_unwrapped_rad[0] if plan.phi_unwrapped_rad else 0)}",
            f"M200 S{_f6(plan.rpm)}",
            f"M201 S {_f6(plan.start_phi_rad)} {_f6(plan.start_r_mm)}",
            f"M201 Q{len(plan.segments)}",
        ]
        for seg in plan.segments:
            lines.append(f"M201 P {_f6(seg.r1_mm)} {_f6(seg.d_theta_rad)}")
        lines.append("M202")
        return "\n".join(lines) + "\n"


def _dist_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    if len2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / len2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
