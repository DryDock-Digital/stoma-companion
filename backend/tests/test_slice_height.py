"""P2-4 automatic slice height. A stoma rising from a thick peristomal-skin slab:
a fixed mid-slice lands in the skin (wrong), auto-height finds the base. Scored on
the P2-1 diameter board. Real-fixture tuning deferred (P0-3)."""

from __future__ import annotations

import json

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from app.measure import slice_height  # noqa: E402
from app.verify import discover_fixtures, run_scoreboard  # noqa: E402
from app.verify.harness import METHODS  # noqa: E402

R_STOMA = 16.5  # → 33 mm base diameter
SKIN_RADIUS = 40.0
STOMA_H = 15.0
SKIN_THICK = 20.0


def _stoma_on_skin():
    """Skin slab (radius 40) spanning z∈[-20,0] with a stoma cylinder (radius 16.5)
    rising z∈[0,15]. Base diameter = 33 mm at the z=0 junction."""
    skin = trimesh.creation.cylinder(radius=SKIN_RADIUS, height=SKIN_THICK, sections=48)
    skin.apply_translation([0, 0, -SKIN_THICK / 2])
    stoma = trimesh.creation.cylinder(radius=R_STOMA, height=STOMA_H, sections=48)
    stoma.apply_translation([0, 0, STOMA_H / 2])
    mesh = trimesh.util.concatenate([skin, stoma])
    return np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int), mesh


def test_area_profile_has_skin_to_stoma_drop():
    v, f, _ = _stoma_on_skin()
    fracs, areas = slice_height.area_profile(v, f, [0, 0, 1], n=48)
    # skin cross-section (π·40²) is much larger than the stoma (π·16.5²)
    assert areas.max() > 3 * np.pi * R_STOMA**2
    # a clear downward step exists (the junction)
    steps = areas[:-1] - areas[1:]
    assert steps.max() > 0.2 * areas.max()


def test_auto_slice_fraction_lands_in_stoma():
    v, f, _ = _stoma_on_skin()
    frac = slice_height.auto_slice_fraction(v, f, [0, 0, 1])
    # junction at z=0 is fraction 20/35 ≈ 0.571; base sits just above it
    assert 0.57 < frac < 0.75


def _fixture_dir(tmp_path):
    d = tmp_path / "stoma_on_skin"
    d.mkdir()
    _, _, mesh = _stoma_on_skin()
    mesh.export(d / "mesh.obj")
    (d / "truth.json").write_text(json.dumps({"metric": "diameter", "diameter_mm": 2 * R_STOMA}))
    (d / "params.json").write_text(json.dumps({"up_axis": "positiveZ"}))  # no manual offset
    return tmp_path


def test_auto_height_passes_where_fixed_midslice_fails(tmp_path):
    fixtures = discover_fixtures(_fixture_dir(tmp_path))

    baseline = run_scoreboard(fixtures, METHODS["baseline"], tolerance_mm=1.0).results[0]
    auto = run_scoreboard(fixtures, METHODS["auto-height"], tolerance_mm=1.0).results[0]

    # baseline's default 0.5 slice hits the skin slab → ~2·40 = 80 mm, far off
    assert not baseline.passed
    assert baseline.measured_mm > 60
    # auto-height finds the base → 33 mm
    assert auto.passed
    assert auto.measured_mm == pytest.approx(33.0, abs=0.5)
