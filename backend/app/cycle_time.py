"""Cycle-time budget instrumentation (P2-6, FR-11 ≤ 2 min).

A `StageTimer` times each pipeline stage; a `CycleReport` rolls the timings into a
budget table against the 120 s target and names the bottleneck. The instrumentation
goes in now (keyframe extract + reconstruction record their seconds onto the job;
the sweep rig times reconstruction) so the *first* real video through the pipeline
yields honest per-stage numbers — no fabricated timings here.

    stoma-cycle-budget run-timings.json      # render a budget from recorded timings
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TARGET_S = 120.0  # FR-11: measure-to-cut cycle target

# Canonical stage order for display; extra stages are appended in first-seen order.
CANONICAL_STAGES = ["extract", "reconstruct", "scale", "orient", "slice", "outline", "gcode"]


@dataclass
class StageTiming:
    name: str
    seconds: float


@dataclass
class StageTimer:
    """Times named stages. `clock` is injectable so tests are deterministic."""

    clock: Callable[[], float] = time.perf_counter
    timings: list[StageTiming] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str):
        start = self.clock()
        try:
            yield
        finally:
            self.record(name, self.clock() - start)

    def record(self, name: str, seconds: float) -> None:
        self.timings.append(StageTiming(name, seconds))

    def get(self, name: str) -> float:
        return sum(t.seconds for t in self.timings if t.name == name)

    def as_dict(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in self.timings:
            out[t.name] = out.get(t.name, 0.0) + t.seconds
        return out


def merge_timing(existing_result: dict | None, stage: str, seconds: float) -> dict:
    """Merge a stage timing into a job `result` dict (stages accumulate under
    `timings_s`). Used by the keyframe stage + reconstruction worker so a job carries
    its own per-stage budget."""
    result = dict(existing_result or {})
    timings = dict(result.get("timings_s", {}))
    timings[stage] = round(timings.get(stage, 0.0) + seconds, 4)
    result["timings_s"] = timings
    return result


@dataclass
class CycleReport:
    timings: dict[str, float]
    target_s: float = DEFAULT_TARGET_S

    @classmethod
    def from_dict(
        cls, timings: dict[str, float], target_s: float = DEFAULT_TARGET_S
    ) -> CycleReport:
        return cls(timings=dict(timings), target_s=target_s)

    @classmethod
    def from_job_result(cls, result: dict[str, Any] | None, target_s: float = DEFAULT_TARGET_S):
        return cls.from_dict((result or {}).get("timings_s", {}), target_s)

    @property
    def total(self) -> float:
        return sum(self.timings.values())

    @property
    def margin(self) -> float:
        """Headroom to the target; negative = over budget."""
        return self.target_s - self.total

    @property
    def within_budget(self) -> bool:
        return self.total <= self.target_s

    @property
    def bottleneck(self) -> str | None:
        return max(self.timings, key=self.timings.get) if self.timings else None

    def _ordered(self) -> list[tuple[str, float]]:
        known = [(s, self.timings[s]) for s in CANONICAL_STAGES if s in self.timings]
        extra = [(s, v) for s, v in self.timings.items() if s not in CANONICAL_STAGES]
        return known + extra

    def to_dict(self) -> dict:
        return {
            "timings_s": self.timings,
            "total_s": round(self.total, 3),
            "target_s": self.target_s,
            "margin_s": round(self.margin, 3),
            "within_budget": self.within_budget,
            "bottleneck": self.bottleneck,
        }

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["stage", "seconds", "pct_of_total"])
        total = self.total or 1.0
        for name, sec in self._ordered():
            w.writerow([name, f"{sec:.3f}", f"{100 * sec / total:.1f}"])
        w.writerow(["TOTAL", f"{self.total:.3f}", "100.0"])
        return buf.getvalue()

    def format_table(self) -> str:
        if not self.timings:
            return "No stage timings yet — run a real video through the pipeline (P0-3)."
        total = self.total or 1.0
        rows = [f"{'stage':<14}{'sec':>9}{'% total':>9}  bar"]
        rows.append("-" * 46)
        for name, sec in self._ordered():
            pct = 100 * sec / total
            bar = "█" * max(1, round(pct / 4)) if sec > 0 else ""
            mark = "  ← bottleneck" if name == self.bottleneck else ""
            rows.append(f"{name:<14}{sec:>9.3f}{pct:>8.1f}%  {bar}{mark}")
        rows.append("-" * 46)
        verdict = "within" if self.within_budget else "OVER"
        rows.append(
            f"{'TOTAL':<14}{self.total:>9.3f}   target ±{self.target_s:g}s  "
            f"→ {verdict} budget (margin {self.margin:+.1f}s)"
        )
        return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stoma-cycle-budget",
        description="Render a cycle-time budget from recorded stage timings (P2-6).",
    )
    parser.add_argument(
        "timings",
        help="JSON file: either {stage: seconds} or a job result with timings_s",
    )
    parser.add_argument(
        "--target", type=float, default=DEFAULT_TARGET_S, help="target seconds (default 120)"
    )
    parser.add_argument("--csv", help="also write the budget as CSV")
    args = parser.parse_args(argv)

    with open(args.timings) as fh:
        data = json.load(fh)
    timings = data.get("timings_s", data)  # accept a raw map or a job result
    report = CycleReport.from_dict(timings, target_s=args.target)

    print(report.format_table())
    if args.csv:
        from pathlib import Path

        Path(args.csv).write_text(report.to_csv())
        print(f"\nwrote {args.csv}")
    return 0 if report.within_budget else 1


if __name__ == "__main__":
    sys.exit(main())
