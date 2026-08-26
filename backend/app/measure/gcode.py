"""G-code generation (P1-8, P4-ready).

Two exporters ported from the legacy Mac app plus a controller-dialect layer:

  - `perimeter_gcode` / `ideal_fit_gcode`  ← CompanionMac/BasePerimeterExport.swift
    A continuous G1 chain around a closed outline. The *wafer* cut is the Ideal-Fit
    ring (base outline + FR-07 grace ring) — `measure_scan` emits that one; the raw
    base perimeter export exists for parity/debug only.
  - `PolarPathExport` (build + polar_file_text)  ← CompanionMac/PolarPathExport.swift
    Fixed-ω polar plan for the legacy platter plotter (M200/M201/M202 custom format).
    **Not G-code** — GRBL rejects it. Kept for legacy-fixture parity only.

Units. The legacy Swift exported metre-unit meshes and multiplied by 1000 to get mm.
The Python pipeline works in **millimetres end to end**, so every ring passed here is
already mm unless the caller says otherwise via `input_units="m"`. That flag exists
for the legacy-fixture parity path and nothing else.

Dialects (`GcodeDialect`). The legacy target was Cole's custom StomaPlotter firmware
(G28 = home-then-centre, arc lead-in from the centre, Z ignored). GRBL means
something different by G28 ("go to the stored G28 position"), needs a work-origin at
the platter centre, and needs explicit plunge/retract. Both are expressed as data so
Remedy's example file (P4-4) becomes a third dialect, not a rewrite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .slicing import SAMPLE_COUNT, PerimeterResult

Point = tuple[float, float]

GCODE_FEED_MM_PER_MIN = 1200

# Sanity bound on emitted coordinates: a stoma outline plus ring fits well inside a
# 100 mm square. Anything larger means a units mistake upstream (the ×1000 bug class).
MAX_ABS_COORD_MM = 150.0


class GcodeUnitsError(ValueError):
    pass


def _f6(v: float) -> str:
    return f"{v:.6f}"


def _f3(v: float) -> str:
    return f"{v:.3f}"


# --- dialects --------------------------------------------------------------


@dataclass(frozen=True)
class GcodeDialect:
    """Everything controller-specific, as data.

    `preamble` runs before the first motion; `lead_in` decides how we reach P1
    (StomaPlotter arcs from the centre on its own; GRBL needs a G0 rapid + plunge);
    `postamble` runs after the closing move.
    """

    name: str
    preamble: tuple[str, ...]
    postamble: tuple[str, ...]
    feed_mm_per_min: float = GCODE_FEED_MM_PER_MIN
    #: emit "G0 X Y" to the first point before cutting (GRBL) vs rely on firmware (legacy)
    rapid_to_start: bool = False
    #: Z to plunge to for the cut (None → no Z words at all)
    cut_z_mm: float | None = None
    #: Z to retract to before/after the cut (used only with cut_z_mm)
    safe_z_mm: float | None = None
    #: emit Z0.000000 on every G1 (legacy StomaPlotter behaviour)
    legacy_z_words: bool = False
    #: set the work origin (G92 X0 Y0) at the platter centre before cutting
    set_work_origin: bool = False
    #: spindle/knife on-off words (e.g. ("M3 S1000",), ("M5",)); empty for none
    tool_on: tuple[str, ...] = field(default_factory=tuple)
    tool_off: tuple[str, ...] = field(default_factory=tuple)


STOMA_PLOTTER = GcodeDialect(
    name="stoma-plotter",
    preamble=("G28", "G21", "G90"),
    postamble=("M2",),
    legacy_z_words=True,
)

# Conservative generic GRBL 1.1 / grblHAL program: mm, absolute, XY plane, work
# origin at the platter centre, safe-Z rapid to P1, plunge, cut, retract, end.
# Feed/depth/knife words are parameters — Remedy's machine info (P4-4) fills them.
GRBL = GcodeDialect(
    name="grbl",
    preamble=("G21", "G90", "G17", "G94"),
    postamble=("M30",),
    rapid_to_start=True,
    cut_z_mm=-1.0,
    safe_z_mm=5.0,
    set_work_origin=True,
    tool_on=(),
    tool_off=(),
)

DIALECTS: dict[str, GcodeDialect] = {STOMA_PLOTTER.name: STOMA_PLOTTER, GRBL.name: GRBL}


def get_dialect(name: str) -> GcodeDialect:
    try:
        return DIALECTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown G-code dialect {name!r}; known: {sorted(DIALECTS)}") from exc


# --- units -----------------------------------------------------------------


def spatial_multiplier(input_units: str, user_scale: float = 1.0) -> float:
    """Multiplier that takes ring coordinates in `input_units` to millimetres.
    'mm' → ×1 (the pipeline default); 'm' → ×1000 (legacy metre meshes)."""
    if input_units == "mm":
        base = 1.0
    elif input_units == "m":
        base = 1000.0
    else:
        raise GcodeUnitsError(f"input_units must be 'mm' or 'm', got {input_units!r}")
    return base * user_scale


def ring_to_mm(ring: list[Point], input_units: str = "mm", user_scale: float = 1.0) -> list[Point]:
    mul = spatial_multiplier(input_units, user_scale)
    return [(x * mul, y * mul) for x, y in ring]


def _check_bounds(ring_mm: list[Point]) -> None:
    worst = max((max(abs(x), abs(y)) for x, y in ring_mm), default=0.0)
    if worst > MAX_ABS_COORD_MM:
        raise GcodeUnitsError(
            f"outline coordinate {worst:.1f} mm exceeds {MAX_ABS_COORD_MM:g} mm — "
            "almost certainly a units mistake (metres vs millimetres) upstream"
        )


# --- program builder -------------------------------------------------------


def _program(
    ring_mm: list[Point],
    result: PerimeterResult | None,
    dialect: GcodeDialect,
    header_extra: str | None,
    multiplier_note: str,
) -> str:
    if not ring_mm:
        return "; Empty perimeter\n"
    _check_bounds(ring_mm)

    first = ring_mm[0]
    feed = f" F{dialect.feed_mm_per_min:g}"
    z_word = " Z0.000000" if dialect.legacy_z_words else ""

    lines: list[str] = ["; Stoma Companion wafer outline"]
    if header_extra:
        lines.append(header_extra)
    lines.append(f"; dialect = {dialect.name}")
    lines.append(
        "; Vertices in slice plane: X along axisU, Y along axisV, origin at the outline's "
        "polar origin (platter centre)."
    )
    if result is not None:
        c, n = result.centroid_world, result.plane_normal
        au, av = result.axis_u, result.axis_v
        lines.append(f"; polar_origin_world_mm = ({_f6(c[0])}, {_f6(c[1])}, {_f6(c[2])})")
        lines.append(f"; planeNormal = ({_f6(n[0])}, {_f6(n[1])}, {_f6(n[2])})")
        lines.append(f"; axisU = ({_f6(au[0])}, {_f6(au[1])}, {_f6(au[2])})")
        lines.append(f"; axisV = ({_f6(av[0])}, {_f6(av[1])}, {_f6(av[2])})")
        lines.append(
            f"; samples = {len(ring_mm)} loopVertices = {result.loop_vertex_count} "
            f"sliceOffsetFraction = {_f6(result.slice_offset_fraction)}"
        )
    lines.append("; path: equal arc-length spacing along closed outline (continuous G1 chain)")
    lines.append(f"; {multiplier_note}")
    lines.append(f"; Units: millimetres (G21). G1 feed F{dialect.feed_mm_per_min:g} mm/min.")
    if dialect.cut_z_mm is not None:
        lines.append(f"; Z: safe {dialect.safe_z_mm:g} mm, cut {dialect.cut_z_mm:g} mm")
    lines.append("")

    lines.extend(dialect.preamble)
    if dialect.set_work_origin:
        lines.append("; Work origin: platter centre. Jog the tool over the centre before running.")
        lines.append("G92 X0 Y0")
    if dialect.cut_z_mm is not None and dialect.safe_z_mm is not None:
        lines.append(f"G0 Z{_f3(dialect.safe_z_mm)}")
    if dialect.rapid_to_start:
        lines.append(f"G0 X{_f6(first[0])} Y{_f6(first[1])}")
    else:
        lines.append("; Lead-in: firmware arcs from centre to first point (no G0 to P1).")
    lines.extend(dialect.tool_on)
    if dialect.cut_z_mm is not None:
        lines.append(f"G1 Z{_f3(dialect.cut_z_mm)}{feed}")

    lines.append(f"G1 X{_f6(first[0])} Y{_f6(first[1])}{z_word}{feed}")
    for p in ring_mm[1:]:
        lines.append(f"G1 X{_f6(p[0])} Y{_f6(p[1])}{z_word}{feed}")
    lines.append(f"G1 X{_f6(first[0])} Y{_f6(first[1])}{z_word}{feed}")

    if dialect.cut_z_mm is not None and dialect.safe_z_mm is not None:
        lines.append(f"G0 Z{_f3(dialect.safe_z_mm)}")
    lines.extend(dialect.tool_off)
    lines.extend(dialect.postamble)
    return "\n".join(lines) + "\n"


def perimeter_gcode(
    result: PerimeterResult,
    *,
    input_units: str = "mm",
    user_scale: float = 1.0,
    dialect: GcodeDialect = STOMA_PLOTTER,
) -> str:
    """Raw base-perimeter program (BasePerimeterExport.gcodeBlock). Debug/parity
    only — this cuts a stoma-sized hole with zero clearance. The wafer uses
    `ideal_fit_gcode`."""
    ring = ring_to_mm(result.plane_xy(), input_units, user_scale)
    mul = spatial_multiplier(input_units, user_scale)
    return _program(
        ring,
        result,
        dialect,
        header_extra="; Outline: base perimeter (NO clearance — not the wafer cut)",
        multiplier_note=f"input_units = {input_units} user_linear_scale = {user_scale} "
        f"combined_spatial_multiplier = {mul}",
    )


def ideal_fit_gcode(
    ideal_ring: list[Point],
    result: PerimeterResult | None = None,
    *,
    clearance_mm: float | None = None,
    input_units: str = "mm",
    user_scale: float = 1.0,
    dialect: GcodeDialect = STOMA_PLOTTER,
) -> str:
    """Wafer-cut program: the Ideal-Fit ring (base outline offset by the FR-07
    grace ring). This is what the machine cuts."""
    ring = ring_to_mm(ideal_ring, input_units, user_scale)
    mul = spatial_multiplier(input_units, user_scale)
    ring_note = f" clearance_mm = {clearance_mm:g}" if clearance_mm is not None else ""
    return _program(
        ring,
        result,
        dialect,
        header_extra=f"; Outline: Ideal Fit (wafer cut, outward clearance from base){ring_note}",
        multiplier_note=f"input_units = {input_units} user_linear_scale = {user_scale} "
        f"combined_spatial_multiplier = {mul}",
    )


def parse_xy(text: str) -> list[Point]:
    """XY of every G1 move in a program — for tests and for the P4 sender's preview."""
    pts: list[Point] = []
    for ln in text.splitlines():
        if not ln.startswith("G1 "):
            continue
        x = y = None
        for word in ln.split():
            if word.startswith("X"):
                x = float(word[1:])
            elif word.startswith("Y"):
                y = float(word[1:])
        if x is not None and y is not None:
            pts.append((x, y))
    return pts


# --- Polar path plan (PolarPathExport) — legacy platter format, NOT G-code ----


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
    """Legacy StomaPlotter polar plan (M200/M201/M202). Not consumed by GRBL;
    retained for fixture parity with the Mac app's exports."""

    FORMAT_VERSION = "stoma_polar_v1"
    DEFAULT_RPM = 3.0
    DEFAULT_CENTER_FROM_HOME_MM = 38.0  # legacy platter geometry; parameter, not a constant
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
        center_from_home_mm: float | None = None,
    ) -> PolarPathPlan | None:
        if len(plane_xy) < 3:
            return None
        rpm = cls.DEFAULT_RPM if rpm is None else rpm
        max_v_gate = (
            cls.DEFAULT_MAX_RADIAL_SPEED_MM_S
            if max_radial_speed_mm_s is None
            else max_radial_speed_mm_s
        )
        center_off = (
            cls.DEFAULT_CENTER_FROM_HOME_MM if center_from_home_mm is None else center_from_home_mm
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
            center_offset_mm=center_off,
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


__all__ = [
    "GRBL",
    "STOMA_PLOTTER",
    "DIALECTS",
    "GcodeDialect",
    "GcodeUnitsError",
    "PolarPathExport",
    "PolarPathPlan",
    "SAMPLE_COUNT",
    "get_dialect",
    "ideal_fit_gcode",
    "parse_xy",
    "perimeter_gcode",
    "ring_to_mm",
    "spatial_multiplier",
]
