"""P2-6 cycle-time budget. Deterministic via an injected clock — no real sleeps."""

from __future__ import annotations

from app.cycle_time import CycleReport, StageTimer, merge_timing


def _clock(values):
    it = iter(values)
    return lambda: next(it)


def test_stage_timer_records_durations():
    timer = StageTimer(clock=_clock([0.0, 2.0, 10.0, 15.0]))
    with timer.stage("a"):
        pass
    with timer.stage("b"):
        pass
    assert timer.get("a") == 2.0
    assert timer.get("b") == 5.0
    assert timer.as_dict() == {"a": 2.0, "b": 5.0}


def test_merge_timing_accumulates():
    r = merge_timing(None, "extract", 5.0)
    r = merge_timing(r, "reconstruct", 90.0)
    r = merge_timing(r, "extract", 1.0)  # same stage accumulates
    assert r["timings_s"] == {"extract": 6.0, "reconstruct": 90.0}


def test_cycle_report_budget_math():
    report = CycleReport.from_dict({"extract": 5, "reconstruct": 90, "slice": 1}, target_s=120)
    assert report.total == 96
    assert report.margin == 24
    assert report.within_budget
    assert report.bottleneck == "reconstruct"

    table = report.format_table()
    assert "reconstruct" in table and "← bottleneck" in table
    assert "within budget" in table


def test_cycle_report_over_budget():
    report = CycleReport.from_dict({"reconstruct": 200}, target_s=120)
    assert not report.within_budget
    assert report.margin == -80
    assert "OVER budget" in report.format_table()


def test_from_job_result_and_csv():
    report = CycleReport.from_job_result({"timings_s": {"extract": 3, "reconstruct": 40}})
    assert report.total == 43
    csv = report.to_csv()
    assert csv.splitlines()[0] == "stage,seconds,pct_of_total"
    assert "reconstruct" in csv and "TOTAL" in csv


def test_empty_report_is_graceful():
    assert "No stage timings yet" in CycleReport.from_dict({}).format_table()
