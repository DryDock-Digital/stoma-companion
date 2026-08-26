"""Scoring harness (P2-1): measured-vs-truth deviation against the ±1 mm tolerance.

A `MeasurementMethod` turns a fixture into a measured value (mm). The harness runs
it over every fixture, compares to caliper truth, and aggregates into a
`Scoreboard`. P2-2…P2-4 each add a method and are scored on the same board.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Protocol

from ..measure import slice_height, slicing
from .fixtures import Fixture

DEFAULT_TOLERANCE_MM = 1.0  # FR-09 dimensional tolerance


@dataclass
class MeasuredResult:
    value_mm: float
    metric: str
    meta: dict


class MeasurementMethod(Protocol):
    name: str

    def measure(self, fixture: Fixture) -> MeasuredResult: ...


# --- diameter methods ------------------------------------------------------


def _normal_from_params(p: dict):
    """Slice up-axis from fixture params (manual tilt or a fixed axis)."""
    if "tilt" in p:
        t = p["tilt"]
        return slicing.plane_normal_from_manual_tilt(
            t.get("base_axis", "positiveY"),
            float(t.get("tilt_x", 0.0)),
            float(t.get("tilt_y", 0.0)),
            float(t.get("tilt_z", 0.0)),
        )
    return slicing.FIXED_AXES[p.get("up_axis", "positiveZ")]


def _diameter(
    fixture: Fixture, vertices, faces, normal, fraction, spin, extra_meta
) -> MeasuredResult:
    result = slicing.extract_perimeter(vertices, faces, normal, fraction, spin_degrees=spin)
    diameter_scene = result.diameter()  # exact: max chord over the raw loop vertices
    return MeasuredResult(
        value_mm=diameter_scene * fixture.scale_mm_per_unit,
        metric="diameter",
        meta={
            "diameter_scene": diameter_scene,
            "scale_mm_per_unit": fixture.scale_mm_per_unit,
            "loop_vertices": result.loop_vertex_count,
            "slice_offset_fraction": fraction,
            **extra_meta,
        },
    )


class BaselineDiameterMethod:
    """Diameter via the ported manual-slice pipeline (P1-7). Reads slice params from
    the fixture (up axis / tilt, offset fraction, spin); defaults to a +Z mid-slice.
    Diameter = longest planar chord × scale (scene units → mm)."""

    name = "baseline-manual-slice"

    def measure(self, fixture: Fixture) -> MeasuredResult:
        vertices, faces = fixture.load_mesh()
        p = fixture.params
        normal = _normal_from_params(p)
        offset = float(p.get("slice_offset_fraction", 0.5))
        spin = float(p.get("spin_degrees", 0.0))
        return _diameter(fixture, vertices, faces, normal, offset, spin, {})


class AutoHeightDiameterMethod:
    """Diameter at the *automatically* detected base height (P2-4): the skin→stoma
    junction from the area profile, no manual offset. Orientation still comes from
    params here; in the full pipeline it comes from P2-2/P2-3."""

    name = "auto-height"

    def measure(self, fixture: Fixture) -> MeasuredResult:
        vertices, faces = fixture.load_mesh()
        p = fixture.params
        normal = _normal_from_params(p)
        spin = float(p.get("spin_degrees", 0.0))
        fraction = slice_height.auto_slice_fraction(vertices, faces, normal)
        return _diameter(fixture, vertices, faces, normal, fraction, spin, {"auto_height": True})


METHODS: dict[str, MeasurementMethod] = {
    "baseline": BaselineDiameterMethod(),
    "auto-height": AutoHeightDiameterMethod(),
}


# --- results + scoreboard --------------------------------------------------


@dataclass
class RunResult:
    fixture: str
    method: str
    metric: str
    truth_mm: float
    measured_mm: float | None
    deviation_mm: float | None  # measured − truth (signed)
    passed: bool
    error: str | None = None

    @property
    def abs_deviation_mm(self) -> float | None:
        return None if self.deviation_mm is None else abs(self.deviation_mm)

    def as_row(self) -> dict:
        return {
            "fixture": self.fixture,
            "method": self.method,
            "metric": self.metric,
            "truth_mm": _fmt(self.truth_mm),
            "measured_mm": _fmt(self.measured_mm),
            "deviation_mm": _fmt(self.deviation_mm),
            "abs_deviation_mm": _fmt(self.abs_deviation_mm),
            "passed": self.passed,
            "error": self.error or "",
        }


@dataclass
class Scoreboard:
    method: str
    tolerance_mm: float
    results: list[RunResult]

    def summary(self) -> dict:
        devs = [r.abs_deviation_mm for r in self.results if r.abs_deviation_mm is not None]
        measured = len(devs)
        passed = sum(1 for r in self.results if r.passed)
        return {
            "fixtures": len(self.results),
            "measured": measured,
            "errors": sum(1 for r in self.results if r.error),
            "passed": passed,
            "failed": measured - passed,
            "mean_abs_dev_mm": (sum(devs) / measured) if measured else None,
            "max_abs_dev_mm": max(devs) if devs else None,
            # headroom to the tolerance; positive = passing with margin (P5 language)
            "margin_mm": (self.tolerance_mm - max(devs)) if devs else None,
        }

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    def to_csv(self) -> str:
        buf = io.StringIO()
        fields = [
            "fixture",
            "method",
            "metric",
            "truth_mm",
            "measured_mm",
            "deviation_mm",
            "abs_deviation_mm",
            "passed",
            "error",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        for r in self.results:
            writer.writerow(r.as_row())
        return buf.getvalue()

    def format_table(self) -> str:
        if not self.results:
            return (
                "No fixtures found. Add them under fixtures/<name>/ per fixtures/README "
                "(mesh.obj + truth.json). Fixtures are blocked on Cole's videos (P0-3)."
            )
        rows = []
        header = f"{'fixture':<20} {'truth':>8} {'measured':>9} {'Δ mm':>8} {'|Δ|':>7}  result"
        rows.append(header)
        rows.append("-" * len(header))
        for r in self.results:
            if r.error:
                dashes = f"{'—':>9} {'—':>8} {'—':>7}"
                rows.append(f"{r.fixture:<20} {r.truth_mm:>8.2f} {dashes}  ERROR: {r.error}")
                continue
            verdict = "PASS" if r.passed else "FAIL"
            rows.append(
                f"{r.fixture:<20} {r.truth_mm:>8.2f} {r.measured_mm:>9.2f} "
                f"{r.deviation_mm:>+8.2f} {r.abs_deviation_mm:>7.2f}  {verdict}"
            )
        s = self.summary()
        rows.append("-" * len(header))
        rows.append(
            f"method={self.method}  tolerance=±{self.tolerance_mm:g} mm  "
            f"{s['passed']}/{s['fixtures']} pass"
            + (f"  ({s['errors']} error)" if s["errors"] else "")
        )
        if s["max_abs_dev_mm"] is not None:
            rows.append(
                f"mean |Δ|={s['mean_abs_dev_mm']:.3f} mm   "
                f"max |Δ|={s['max_abs_dev_mm']:.3f} mm   "
                f"margin={s['margin_mm']:+.3f} mm"
            )
        return "\n".join(rows)


def run_scoreboard(
    fixtures: list[Fixture],
    method: MeasurementMethod,
    tolerance_mm: float = DEFAULT_TOLERANCE_MM,
) -> Scoreboard:
    results: list[RunResult] = []
    for fx in fixtures:
        try:
            truth = fx.truth_mm
        except Exception as exc:  # noqa: BLE001 — bad fixture shouldn't abort the board
            results.append(
                RunResult(fx.name, method.name, "?", float("nan"), None, None, False, str(exc))
            )
            continue
        try:
            measured = method.measure(fx)
            deviation = measured.value_mm - truth
            results.append(
                RunResult(
                    fixture=fx.name,
                    method=method.name,
                    metric=measured.metric,
                    truth_mm=truth,
                    measured_mm=measured.value_mm,
                    deviation_mm=deviation,
                    passed=abs(deviation) <= tolerance_mm,
                )
            )
        except Exception as exc:  # noqa: BLE001 — record the failure, keep scoring
            results.append(
                RunResult(fx.name, method.name, fx.metric, truth, None, None, False, str(exc))
            )
    return Scoreboard(method=method.name, tolerance_mm=tolerance_mm, results=results)


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:.4f}"
