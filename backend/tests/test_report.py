"""P5-2 aggregates + export."""

from __future__ import annotations

from app.runlog import RunRecord
from app.verify.report import headline, summarize, to_csv, to_pdf


def _runs():
    return [
        RunRecord.build(model_name="model-a", measured_mm=33.4, truth_mm=33.0),
        RunRecord.build(model_name="model-b", measured_mm=27.8, truth_mm=28.0),
        RunRecord.build(model_name="model-c", measured_mm=41.5, truth_mm=41.0),
    ]


def test_summary_math():
    s = summarize(_runs())
    assert s.count == 3 and s.unique_models == 3 and s.measured == 3
    assert s.passed == 3 and s.pass_rate == 1.0
    assert s.max_abs_dev_mm == 0.5
    assert abs(s.mean_abs_dev_mm - (0.4 + 0.2 + 0.5) / 3) < 1e-9
    assert abs(s.worst_margin_mm - 0.5) < 1e-9  # 1.0 - 0.5
    assert s.all_within_tolerance


def test_summary_flags_failure():
    runs = _runs() + [RunRecord.build(model_name="model-d", measured_mm=36.0, truth_mm=33.0)]
    s = summarize(runs)
    assert not s.all_within_tolerance
    assert s.passed == 3 and s.measured == 4
    assert s.max_abs_dev_mm == 3.0


def test_headline_reads_like_evidence():
    h = headline(summarize(_runs()))
    assert "3 tests across 3 unique stomas" in h
    assert "all within ±1 mm" in h
    assert "average margin" in h


def test_csv_has_header_and_rows():
    csv = to_csv(_runs())
    lines = csv.splitlines()
    assert lines[0].startswith("model_name,reference_point,metric,truth_mm,measured_mm")
    assert len(lines) == 1 + 3
    assert "model-b" in csv


def test_pdf_is_generated():
    pdf = to_pdf(_runs())
    assert isinstance(pdf, bytes)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000  # a real document, not an empty shell


def test_empty_report_is_graceful():
    s = summarize([])
    assert s.count == 0 and s.measured == 0
    assert "no caliper truth yet" in headline(s)
    assert to_pdf([])[:5] == b"%PDF-"
