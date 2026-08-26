"""P2-1 verification harness — validated on synthetic fixtures with known truth.
Real fixture parity joins at P0-3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

trimesh = pytest.importorskip("trimesh")

from app.verify import METHODS, discover_fixtures, run_scoreboard  # noqa: E402
from app.verify.__main__ import main as score_main  # noqa: E402


def _make_fixture(root: Path, name: str, truth_mm: float, radius_mm: float = 16.5):
    """A cylinder of the given radius → circular cross-section of diameter 2·radius,
    sliced perpendicular to its +Z axis."""
    d = root / name
    d.mkdir(parents=True)
    mesh = trimesh.creation.cylinder(radius=radius_mm, height=20.0, sections=96)
    mesh.export(d / "mesh.obj")
    (d / "truth.json").write_text(json.dumps({"metric": "diameter", "diameter_mm": truth_mm}))
    (d / "params.json").write_text(
        json.dumps({"up_axis": "positiveZ", "slice_offset_fraction": 0.5})
    )
    return d


def test_discover_empty(tmp_path):
    assert discover_fixtures(tmp_path) == []
    assert discover_fixtures(tmp_path / "nonexistent") == []


def test_baseline_measures_known_diameter(tmp_path):
    _make_fixture(tmp_path, "cyl_33mm", truth_mm=33.0)  # 16.5 mm radius → 33 mm
    fixtures = discover_fixtures(tmp_path)
    assert len(fixtures) == 1

    board = run_scoreboard(fixtures, METHODS["baseline"], tolerance_mm=1.0)
    r = board.results[0]
    assert r.error is None
    assert r.measured_mm == pytest.approx(33.0, abs=0.2)
    assert r.passed
    assert board.all_passed


def test_out_of_tolerance_fails(tmp_path):
    # mesh is 33 mm but truth claims 40 mm → 7 mm deviation → FAIL
    _make_fixture(tmp_path, "mismatch", truth_mm=40.0)
    board = run_scoreboard(discover_fixtures(tmp_path), METHODS["baseline"], tolerance_mm=1.0)
    r = board.results[0]
    assert not r.passed
    assert r.abs_deviation_mm == pytest.approx(7.0, abs=0.3)
    assert not board.all_passed


def test_scale_is_applied(tmp_path):
    # mesh in "scene units" a tenth of mm; scale 0.1 mm/unit brings 330→33.
    d = _make_fixture(tmp_path, "scaled", truth_mm=33.0, radius_mm=165.0)
    (d / "scale.json").write_text(json.dumps({"scale_mm_per_scene_unit": 0.1}))
    board = run_scoreboard(discover_fixtures(tmp_path), METHODS["baseline"], tolerance_mm=1.0)
    assert board.results[0].measured_mm == pytest.approx(33.0, abs=0.2)
    assert board.results[0].passed


def test_summary_and_csv(tmp_path):
    _make_fixture(tmp_path, "a_pass", truth_mm=33.0)
    _make_fixture(tmp_path, "b_fail", truth_mm=40.0)
    board = run_scoreboard(discover_fixtures(tmp_path), METHODS["baseline"], tolerance_mm=1.0)
    s = board.summary()
    assert s["fixtures"] == 2
    assert s["passed"] == 1 and s["failed"] == 1
    assert s["max_abs_dev_mm"] == pytest.approx(7.0, abs=0.3)
    assert s["margin_mm"] < 0  # at least one fixture blows the tolerance

    csv = board.to_csv()
    assert csv.splitlines()[0].startswith("fixture,method,metric")
    assert "a_pass" in csv and "b_fail" in csv


def test_cli_exit_codes(tmp_path):
    _make_fixture(tmp_path, "cyl_33mm", truth_mm=33.0)
    assert score_main(["--fixtures", str(tmp_path)]) == 0
    _make_fixture(tmp_path, "bad", truth_mm=40.0)
    assert score_main(["--fixtures", str(tmp_path)]) == 1
    # no fixtures → exit 0 (nothing to fail yet)
    assert score_main(["--fixtures", str(tmp_path / "empty")]) == 0
