"""Keyframe-minimization experiment rig (P2-5, FR-11).

The question — "how few frames before real reconstruction degrades?" — is empirical
and about COLMAP's behaviour on *genuine* footage, so synthetic data can't answer it.
What's buildable now is the rig: rerun reconstruction + measurement at several frame
counts and record deviation-vs-truth and runtime-vs-count. When footage arrives it's
one command instead of a week of setup.

The reconstruction engine is injected (the `Reconstructor` contract from app.queue),
so the rig is validated here with a fake engine and runs for real on the worker image
with COLMAP. No synthetic "answer" is invented — only the harness.

    stoma-keyframe-sweep --keyframes ./frames --truth 33.0 --frames 20,50,100,350
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..cycle_time import StageTimer
from ..measure import slicing
from ..measure.slice_height import base_diameter
from .fixtures import load_mesh

DEFAULT_FRAME_COUNTS = (20, 50, 100, 350)


class Reconstructor(Protocol):
    name: str

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> Path: ...


def subsample_frames(frame_paths: list[Path], n: int) -> list[Path]:
    """Evenly pick `n` frames across the sequence (endpoints included)."""
    if n >= len(frame_paths):
        return list(frame_paths)
    idx = np.unique(np.linspace(0, len(frame_paths) - 1, n).round().astype(int))
    return [frame_paths[i] for i in idx]


@dataclass
class SweepRow:
    frame_count: int
    reconstruct_s: float
    measure_s: float
    diameter_mm: float | None
    deviation_mm: float | None
    passed: bool
    error: str | None = None

    @property
    def total_s(self) -> float:
        return self.reconstruct_s + self.measure_s


@dataclass
class SweepResult:
    rows: list[SweepRow]
    truth_mm: float
    tolerance_mm: float

    @property
    def min_passing_frames(self) -> int | None:
        passing = [r.frame_count for r in self.rows if r.passed]
        return min(passing) if passing else None

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "frame_count",
                "reconstruct_s",
                "measure_s",
                "total_s",
                "diameter_mm",
                "deviation_mm",
                "passed",
                "error",
            ]
        )
        for r in self.rows:
            w.writerow(
                [
                    r.frame_count,
                    f"{r.reconstruct_s:.3f}",
                    f"{r.measure_s:.3f}",
                    f"{r.total_s:.3f}",
                    "" if r.diameter_mm is None else f"{r.diameter_mm:.3f}",
                    "" if r.deviation_mm is None else f"{r.deviation_mm:.3f}",
                    r.passed,
                    r.error or "",
                ]
            )
        return buf.getvalue()

    def format_table(self) -> str:
        head = (
            f"{'frames':>7}{'recon s':>10}{'meas s':>9}{'total s':>9}"
            f"{'diam mm':>9}{'Δ mm':>8}  result"
        )
        rows = [head, "-" * len(head)]
        for r in self.rows:
            if r.error:
                dash = f"{'—':>9}{'—':>9}{'—':>9}{'—':>8}"
                rows.append(f"{r.frame_count:>7}{r.reconstruct_s:>10.2f}{dash}  ERROR: {r.error}")
                continue
            verdict = "PASS" if r.passed else "FAIL"
            rows.append(
                f"{r.frame_count:>7}{r.reconstruct_s:>10.2f}{r.measure_s:>9.2f}{r.total_s:>9.2f}"
                f"{r.diameter_mm:>9.2f}{r.deviation_mm:>+8.2f}  {verdict}"
            )
        rows.append("-" * len(head))
        mpf = self.min_passing_frames
        rows.append(
            f"truth={self.truth_mm:g} mm  tolerance=±{self.tolerance_mm:g} mm  "
            + (f"min frames within tolerance: {mpf}" if mpf else "no frame count within tolerance")
        )
        rows.append("")
        rows.append(self._ascii_plots())
        return "\n".join(rows)

    def _ascii_plots(self) -> str:
        ok = [r for r in self.rows if r.error is None]
        if not ok:
            return ""
        lines = ["deviation vs frames (│ = ±tolerance):"]
        dmax = max([abs(r.deviation_mm) for r in ok] + [self.tolerance_mm]) or 1.0
        tol_col = round(30 * self.tolerance_mm / dmax)
        for r in ok:
            n = round(30 * abs(r.deviation_mm) / dmax)
            bar = list("·" * 31)
            bar[min(tol_col, 30)] = "│"
            for i in range(min(n, 30) + 1):
                bar[i] = "▓" if r.passed else "▒"
            lines.append(f"  {r.frame_count:>4}f {''.join(bar)} {r.deviation_mm:+.2f}")
        lines.append("runtime vs frames:")
        tmax = max(r.total_s for r in ok) or 1.0
        for r in ok:
            n = round(30 * r.total_s / tmax)
            lines.append(f"  {r.frame_count:>4}f {'█' * n} {r.total_s:.1f}s")
        return "\n".join(lines)


def run_keyframe_sweep(
    frame_paths: list[Path],
    truth_mm: float,
    reconstructor: Reconstructor,
    *,
    normal=(0.0, 0.0, 1.0),
    scale: float = 1.0,
    frame_counts=DEFAULT_FRAME_COUNTS,
    tolerance_mm: float = 1.0,
    auto_height: bool = True,
    clock=time.perf_counter,
) -> SweepResult:
    rows: list[SweepRow] = []
    for target in frame_counts:
        subset = subsample_frames(frame_paths, target)
        timer = StageTimer(clock)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                kf_dir = Path(tmp) / "kf"
                kf_dir.mkdir()
                for i, src in enumerate(subset):
                    shutil.copy(src, kf_dir / f"frame_{i:05d}.jpg")
                work_dir = Path(tmp) / "work"
                work_dir.mkdir()
                with timer.stage("reconstruct"):
                    mesh_path = reconstructor.reconstruct(kf_dir, work_dir)
                vertices, faces = load_mesh(Path(mesh_path))
                with timer.stage("measure"):
                    diameter = base_diameter(vertices, faces, normal, scale, auto=auto_height)
            deviation = diameter - truth_mm
            rows.append(
                SweepRow(
                    len(subset),
                    timer.get("reconstruct"),
                    timer.get("measure"),
                    diameter,
                    deviation,
                    abs(deviation) <= tolerance_mm,
                )
            )
        except Exception as exc:  # noqa: BLE001 — record, keep sweeping
            rows.append(
                SweepRow(
                    len(subset),
                    timer.get("reconstruct"),
                    timer.get("measure"),
                    None,
                    None,
                    False,
                    str(exc),
                )
            )
    return SweepResult(rows, truth_mm, tolerance_mm)


def _load_reconstructor(name: str) -> Reconstructor:
    if name == "colmap":
        try:
            from reconstruct import (
                ColmapReconstructor,  # worker-colmap module (on the worker image)
            )

            return ColmapReconstructor()
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                "colmap engine unavailable — run this on the worker-colmap image "
                f"(COLMAP + reconstruct.py on PYTHONPATH). {exc}"
            ) from exc
    raise SystemExit(f"unknown engine: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stoma-keyframe-sweep",
        description="Rerun reconstruction+measurement at several frame counts (P2-5).",
    )
    parser.add_argument("--keyframes", type=Path, required=True, help="dir of frame_*.jpg")
    parser.add_argument("--truth", type=float, required=True, help="caliper base diameter (mm)")
    parser.add_argument("--frames", default="20,50,100,350", help="comma list of frame counts")
    parser.add_argument("--engine", default="colmap", help="reconstruction engine (default colmap)")
    parser.add_argument("--up-axis", default="positiveZ", help="slice up-axis (default positiveZ)")
    parser.add_argument("--scale", type=float, default=1.0, help="scene-units→mm scale")
    parser.add_argument("--tolerance", type=float, default=1.0, help="±mm (FR-09)")
    parser.add_argument(
        "--no-auto-height", action="store_true", help="use a fixed mid-slice instead of P2-4"
    )
    parser.add_argument("--csv", type=Path, help="write per-run CSV")
    args = parser.parse_args(argv)

    frame_paths = sorted(args.keyframes.glob("frame_*.jpg"))
    if not frame_paths:
        raise SystemExit(f"no frame_*.jpg under {args.keyframes}")
    counts = [int(x) for x in args.frames.split(",")]

    result = run_keyframe_sweep(
        frame_paths,
        args.truth,
        _load_reconstructor(args.engine),
        normal=slicing.FIXED_AXES[args.up_axis],
        scale=args.scale,
        frame_counts=counts,
        tolerance_mm=args.tolerance,
        auto_height=not args.no_auto_height,
    )
    print(result.format_table())
    if args.csv:
        args.csv.write_text(result.to_csv())
        print(f"\nwrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
