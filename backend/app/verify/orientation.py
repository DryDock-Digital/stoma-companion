"""Orientation scoreboard (P2-2).

Renders a suite of synthetic ArUco scenes with known plane normals, recovers the
normal from multi-view detection + triangulation, and scores the angular error
against truth. Mirrors the P2-1 scoreboard style but the metric is degrees of
orientation error rather than mm of diameter.

    stoma-score-orientation            # score the default suite
    python -m app.verify.orientation --tolerance 2.0 --csv orient.csv

The recovered normal is exactly the "up" axis that feeds slicing (FR-04): it drops
into `slicing.extract_perimeter(..., normal=plane.normal, ...)` in place of a manual
up-axis. Wiring that into the P2-1 diameter board needs mesh+marker fixtures, which
arrive with real footage (P0-3) — deferred by design.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import sys
from dataclasses import dataclass

from ..measure.aruco import detect_markers
from ..measure.orientation import angle_between_axes_deg, recover_marker_plane
from .synthetic import SyntheticScene, build_scene

DEFAULT_TOLERANCE_DEG = 2.0


@dataclass
class OrientationRun:
    scene: str
    views_detected: int
    error_deg: float | None
    rms_planarity: float | None
    passed: bool
    error: str | None = None

    def as_row(self) -> dict:
        return {
            "scene": self.scene,
            "views_detected": self.views_detected,
            "error_deg": "" if self.error_deg is None else f"{self.error_deg:.4f}",
            "rms_planarity": "" if self.rms_planarity is None else f"{self.rms_planarity:.5f}",
            "passed": self.passed,
            "error": self.error or "",
        }


@dataclass
class OrientationScoreboard:
    tolerance_deg: float
    results: list[OrientationRun]

    def summary(self) -> dict:
        errs = [r.error_deg for r in self.results if r.error_deg is not None]
        measured = len(errs)
        passed = sum(1 for r in self.results if r.passed)
        return {
            "scenes": len(self.results),
            "measured": measured,
            "passed": passed,
            "failed": measured - passed,
            "errors": sum(1 for r in self.results if r.error),
            "mean_error_deg": (sum(errs) / measured) if measured else None,
            "max_error_deg": max(errs) if errs else None,
            "margin_deg": (self.tolerance_deg - max(errs)) if errs else None,
        }

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    def to_csv(self) -> str:
        buf = io.StringIO()
        fields = ["scene", "views_detected", "error_deg", "rms_planarity", "passed", "error"]
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        for r in self.results:
            writer.writerow(r.as_row())
        return buf.getvalue()

    def format_table(self) -> str:
        header = f"{'scene':<18} {'views':>5} {'err°':>8} {'rms':>8}  result"
        rows = [header, "-" * len(header)]
        for r in self.results:
            if r.error:
                dashes = f"{'—':>8} {'—':>8}"
                rows.append(f"{r.scene:<18} {r.views_detected:>5} {dashes}  ERROR: {r.error}")
                continue
            verdict = "PASS" if r.passed else "FAIL"
            rows.append(
                f"{r.scene:<18} {r.views_detected:>5} {r.error_deg:>8.3f} "
                f"{r.rms_planarity:>8.4f}  {verdict}"
            )
        s = self.summary()
        rows.append("-" * len(header))
        rows.append(
            f"tolerance=±{self.tolerance_deg:g}°  {s['passed']}/{s['scenes']} pass"
            + (f"  ({s['errors']} error)" if s["errors"] else "")
        )
        if s["max_error_deg"] is not None:
            rows.append(
                f"mean err={s['mean_error_deg']:.3f}°   max err={s['max_error_deg']:.3f}°   "
                f"margin={s['margin_deg']:+.3f}°"
            )
        return "\n".join(rows)


def score_scene(
    scene: SyntheticScene, tolerance_deg: float = DEFAULT_TOLERANCE_DEG
) -> OrientationRun:
    cams, obs = [], []
    for cam, img in zip(scene.cameras, scene.render_views(), strict=True):
        marker = next((d for d in detect_markers(img) if d.marker_id == scene.marker_id), None)
        if marker is not None:
            cams.append(cam)
            obs.append(marker.corners_px)
    if len(cams) < 2:
        return OrientationRun(scene.name, len(cams), None, None, False, "too few detections")
    try:
        plane = recover_marker_plane(cams, obs)
    except Exception as exc:  # noqa: BLE001
        return OrientationRun(scene.name, len(cams), None, None, False, str(exc))
    err = angle_between_axes_deg(plane.normal, scene.true_normal)
    return OrientationRun(scene.name, len(cams), err, plane.rms_planarity, err <= tolerance_deg)


def score_suite(
    scenes: list[SyntheticScene], tolerance_deg: float = DEFAULT_TOLERANCE_DEG
) -> OrientationScoreboard:
    return OrientationScoreboard(tolerance_deg, [score_scene(s, tolerance_deg) for s in scenes])


def _normal_from_tilt(tilt_deg: float, azimuth_deg: float) -> tuple[float, float, float]:
    t, a = math.radians(tilt_deg), math.radians(azimuth_deg)
    return (math.sin(t) * math.cos(a), math.sin(t) * math.sin(a), math.cos(t))


def default_suite() -> list[SyntheticScene]:
    """A spread of known orientations: flat, and tilted 10–30° at a few azimuths —
    the range a phone-held capture of peristomal skin plausibly sees."""
    specs = [
        ("flat", 0.0, 0.0),
        ("tilt10_az0", 10.0, 0.0),
        ("tilt20_az45", 20.0, 45.0),
        ("tilt20_az180", 20.0, 180.0),
        ("tilt30_az90", 30.0, 90.0),
        ("tilt30_az270", 30.0, 270.0),
    ]
    return [build_scene(name, _normal_from_tilt(tilt, az)) for name, tilt, az in specs]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stoma-score-orientation",
        description="Score marker-plane orientation recovery (P2-2).",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE_DEG,
        help="max angular error in degrees (default 2.0)",
    )
    parser.add_argument("--csv", help="also write per-scene results as CSV")
    args = parser.parse_args(argv)

    board = score_suite(default_suite(), tolerance_deg=args.tolerance)
    print(board.format_table())
    if args.csv:
        from pathlib import Path

        Path(args.csv).write_text(board.to_csv())
        print(f"\nwrote {args.csv}")
    return 0 if board.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
