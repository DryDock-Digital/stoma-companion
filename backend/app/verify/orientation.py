"""Orientation scoreboard + method comparison (P2-2, P2-3).

Recovers the slice "up" axis (FR-04) three ways and scores each against the known
plane normal of synthetic scenes:

  - aruco   (P2-2): triangulate the marker's corners across views → plane normal.
              Most accurate, but needs the marker visible.
  - ransac  (P2-3): robust plane fit on the peristomal-skin point cloud. Marker-free
              fallback; rejects the stoma bump and reconstruction outliers.
  - pca     (P2-3): least-squares plane over the skin points. Simple, last resort —
              biased by non-planar features.

`compare()` scores all three on one board so a primary + fallback chain can be
picked. No physical capture — real-footage validation deferred (P0-3).

    stoma-score-orientation          # compare all methods on the default suite
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import sys
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..measure.aruco import detect_markers
from ..measure.orientation import (
    angle_between_axes_deg,
    pca_plane_normal,
    ransac_plane_normal,
    recover_marker_plane,
)
from .synthetic import SyntheticScene, build_scene

DEFAULT_TOLERANCE_DEG = 2.0


@dataclass
class Recovery:
    normal: np.ndarray
    detail: str = ""


class OrientationMethod(Protocol):
    name: str

    def recover(self, scene: SyntheticScene) -> Recovery | None: ...


def _cameras_center(scene: SyntheticScene) -> np.ndarray:
    return np.mean([c.center for c in scene.cameras], axis=0)


class ArucoPlaneMethod:
    name = "aruco"

    def recover(self, scene: SyntheticScene) -> Recovery | None:
        cams, obs = [], []
        for cam, img in zip(scene.cameras, scene.render_views(), strict=True):
            m = next((d for d in detect_markers(img) if d.marker_id == scene.marker_id), None)
            if m is not None:
                cams.append(cam)
                obs.append(m.corners_px)
        if len(cams) < 2:
            return None
        plane = recover_marker_plane(cams, obs)
        return Recovery(plane.normal, f"{len(cams)} views")


class PcaMethod:
    name = "pca"

    def recover(self, scene: SyntheticScene) -> Recovery | None:
        if scene.skin_points is None:
            return None
        normal = pca_plane_normal(scene.skin_points, orient_toward=_cameras_center(scene))
        return Recovery(normal, f"{len(scene.skin_points)} pts")


class RansacMethod:
    name = "ransac"

    def __init__(self, threshold: float = 0.8, seed: int = 0) -> None:
        self.threshold = threshold
        self.seed = seed

    def recover(self, scene: SyntheticScene) -> Recovery | None:
        if scene.skin_points is None:
            return None
        res = ransac_plane_normal(
            scene.skin_points,
            threshold=self.threshold,
            seed=self.seed,
            orient_toward=_cameras_center(scene),
        )
        return Recovery(res.normal, f"inliers {res.inlier_fraction:.0%}")


ORIENTATION_METHODS: dict[str, OrientationMethod] = {
    "aruco": ArucoPlaneMethod(),
    "ransac": RansacMethod(),
    "pca": PcaMethod(),
}


# --- scoring ---------------------------------------------------------------


@dataclass
class OrientationRun:
    scene: str
    method: str
    error_deg: float | None
    detail: str
    passed: bool
    error: str | None = None


def score_scene(
    scene: SyntheticScene, method: OrientationMethod, tolerance_deg: float
) -> OrientationRun:
    try:
        rec = method.recover(scene)
    except Exception as exc:  # noqa: BLE001
        return OrientationRun(scene.name, method.name, None, "", False, str(exc))
    if rec is None:
        return OrientationRun(scene.name, method.name, None, "", False, "n/a")
    err = angle_between_axes_deg(rec.normal, scene.true_normal)
    return OrientationRun(scene.name, method.name, err, rec.detail, err <= tolerance_deg)


@dataclass
class OrientationScoreboard:
    method: str
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
            "pass_rate": (passed / measured) if measured else 0.0,
            "mean_error_deg": (sum(errs) / measured) if measured else None,
            "max_error_deg": max(errs) if errs else None,
        }

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)


def score_suite(
    scenes: list[SyntheticScene],
    method: OrientationMethod,
    tolerance_deg: float = DEFAULT_TOLERANCE_DEG,
) -> OrientationScoreboard:
    return OrientationScoreboard(
        method.name, tolerance_deg, [score_scene(s, method, tolerance_deg) for s in scenes]
    )


def compare(
    scenes: list[SyntheticScene],
    methods: dict[str, OrientationMethod] | None = None,
    tolerance_deg: float = DEFAULT_TOLERANCE_DEG,
) -> dict[str, OrientationScoreboard]:
    methods = methods or ORIENTATION_METHODS
    return {name: score_suite(scenes, m, tolerance_deg) for name, m in methods.items()}


# Reliability preference (lower = preferred). The ArUco marker is the *designed*
# scale/orientation reference, so it's primary whenever visible; the skin fits are
# marker-free fallbacks (RANSAC robust, PCA last resort). Sub-0.1° error differences
# on synthetic data are not meaningful signal, so preference — not raw error — breaks
# ties among methods that all pass.
_PREFERENCE = {"aruco": 0, "ransac": 1, "pca": 2}


def recommended_chain(boards: dict[str, OrientationScoreboard]) -> list[str]:
    """Primary + fallbacks: viable methods (higher pass rate) first, ties broken by
    the reliability preference above."""

    def key(name: str):
        s = boards[name].summary()
        return (-s["pass_rate"], _PREFERENCE.get(name, 99))

    return sorted(boards, key=key)


def format_comparison(boards: dict[str, OrientationScoreboard], tolerance_deg: float) -> str:
    methods = list(boards)
    scenes = [r.scene for r in next(iter(boards.values())).results]
    err_by = {m: {r.scene: r for r in boards[m].results} for m in methods}

    head = f"{'scene':<16}" + "".join(f"{m:>12}" for m in methods)
    rows = [head, "-" * len(head)]
    for sc in scenes:
        cells = ""
        for m in methods:
            r = err_by[m][sc]
            cells += f"{('n/a' if r.error_deg is None else f'{r.error_deg:.2f}°'):>12}"
        rows.append(f"{sc:<16}{cells}")
    rows.append("-" * len(head))

    def _deg(v):
        return f"{v:.3f}°" if v is not None else "—"

    def stat_row(label, fn):
        return f"{label:<16}" + "".join(f"{fn(boards[m].summary()):>12}" for m in methods)

    rows.append(stat_row("mean err", lambda s: _deg(s["mean_error_deg"])))
    rows.append(stat_row("max err", lambda s: _deg(s["max_error_deg"])))
    rows.append(stat_row("pass", lambda s: f"{s['passed']}/{s['measured']}"))

    chain = recommended_chain(boards)
    rows.append("")
    rows.append(f"tolerance=±{tolerance_deg:g}°   recommended chain: " + " → ".join(chain))
    return "\n".join(rows)


def to_csv(boards: dict[str, OrientationScoreboard]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["scene", "method", "error_deg", "detail", "passed", "error"])
    for board in boards.values():
        for r in board.results:
            writer.writerow(
                [
                    r.scene,
                    r.method,
                    "" if r.error_deg is None else f"{r.error_deg:.4f}",
                    r.detail,
                    r.passed,
                    r.error or "",
                ]
            )
    return buf.getvalue()


def _normal_from_tilt(tilt_deg: float, azimuth_deg: float) -> tuple[float, float, float]:
    t, a = math.radians(tilt_deg), math.radians(azimuth_deg)
    return (math.sin(t) * math.cos(a), math.sin(t) * math.sin(a), math.cos(t))


def default_suite() -> list[SyntheticScene]:
    """Flat + 10–30° tilts at several azimuths, each with a marker AND a skin point
    cloud (varied outlier seed) so all methods are scored on the same scenes."""
    specs = [
        ("flat", 0.0, 0.0),
        ("tilt10_az0", 10.0, 0.0),
        ("tilt20_az45", 20.0, 45.0),
        ("tilt20_az180", 20.0, 180.0),
        ("tilt30_az90", 30.0, 90.0),
        ("tilt30_az270", 30.0, 270.0),
    ]
    return [
        build_scene(name, _normal_from_tilt(tilt, az), with_skin=True, skin_kwargs={"seed": i})
        for i, (name, tilt, az) in enumerate(specs)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stoma-score-orientation",
        description="Compare marker-plane orientation methods (P2-2/P2-3).",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE_DEG,
        help="max angular error in degrees (default 2.0)",
    )
    parser.add_argument("--csv", help="also write per-run results as CSV")
    args = parser.parse_args(argv)

    boards = compare(default_suite(), tolerance_deg=args.tolerance)
    print(format_comparison(boards, args.tolerance))
    if args.csv:
        from pathlib import Path

        Path(args.csv).write_text(to_csv(boards))
        print(f"\nwrote {args.csv}")

    primary = recommended_chain(boards)[0]
    return 0 if boards[primary].all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
