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


def test_base_is_the_neck_not_the_fillet():
    """Profile of the first real video: skin flare 39→33 mm over the first 2 mm,
    neck ≈ 33.1 at 2.5–3.7 mm, then the bulge. The base must land on the neck."""
    from app.measure.slice_height import SliceHeightParams, base_height_from_profile

    h = np.array([0.7, 1.3, 1.9, 2.5, 3.1, 3.7, 4.3, 4.9, 5.5, 6.1, 6.7, 7.3, 8.0])
    d = np.array([39.4, 35.9, 33.7, 33.1, 33.1, 33.1, 33.2, 33.3, 33.4, 33.5, 33.6, 33.6, 33.7])
    base = base_height_from_profile(h, d, SliceHeightParams())
    assert 2.5 <= base <= 3.7
    assert d[list(h).index(base)] == 33.1


def test_neck_rule_on_flat_profile_stays_near_junction():
    from app.measure.slice_height import SliceHeightParams, base_height_from_profile

    h = np.linspace(0.5, 12, 24)
    d = np.where(h < 2, 50.0, 33.0)  # slab then a perfect cylinder
    base = base_height_from_profile(h, d, SliceHeightParams())
    assert 2.0 <= base <= 7.5 and d[list(h).index(base)] == 33.0


def test_knee_on_model1_profile():
    """Model 1: fillet 39→33 over the first 2 mm, then flat → knee ≈ 2–2.5 mm."""
    from app.measure.slice_height import SliceHeightParams, base_height_from_profile

    h = np.array([0.7, 1.3, 1.9, 2.5, 3.1, 3.7, 4.3, 4.9, 5.5, 6.1, 6.7, 7.3, 8.0])
    d = np.array([39.4, 35.9, 33.7, 33.1, 33.1, 33.1, 33.2, 33.3, 33.4, 33.5, 33.6, 33.6, 33.7])
    base = base_height_from_profile(h, d, SliceHeightParams())
    assert 1.9 <= base <= 3.1


def test_knee_uses_the_narrow_width_when_the_length_is_flat():
    """Figure-8: the longest chord is flat through the fillet; the waist/widths
    still flare. The knee must come from the narrow profile (~2–2.5 mm)."""
    from app.measure.slice_height import SliceHeightParams, base_height_from_profile

    h = np.array([1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0])
    longest = np.array([54.6, 55.0, 57.0, 56.4, 56.0, 55.7, 55.4, 54.7, 54.1, 53.4, 52.7])
    narrow = np.array([37.6, 38.8, 34.2, 33.9, 33.4, 33.1, 32.8, 32.0, 31.6, 31.1, 30.6])
    base = base_height_from_profile(h, longest, SliceHeightParams(), min_widths=narrow)
    assert 1.9 <= base <= 3.0
    # without the narrow profile the flat length gives no knee → stays low
    assert base_height_from_profile(h, longest, SliceHeightParams()) <= 2.0
