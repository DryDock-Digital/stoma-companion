"""P1-10 end-to-end measurement — validated with ground truth: a known-size ArUco
marker (for scale + up) + a stoma of known 33 mm base diameter on a skin slab,
viewed from known poses, including lens distortion and junk geometry. Proves the
measurement half of the chain; reconstruction is COLMAP on the worker."""

from __future__ import annotations

import math

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
cv2 = pytest.importorskip("cv2")
pytest.importorskip("shapely")

from app.measure import gcode  # noqa: E402
from app.measure.measure_scan import (  # noqa: E402
    MarkerNotFound,
    MeasureError,
    MeasureParams,
    measure_scan,
)
from app.measure.orientation import PinholeCamera  # noqa: E402

R_STOMA = 16.5  # → 33 mm base
MARKER_SIDE = 40.0  # scene units; marker_side_mm=40 → scale 1.0


def _scene(dist=None, marker_offset=(70.0, 0.0, 0.0)):
    from app.verify import synthetic

    scene = synthetic.build_scene(
        "flat", (0.0, 0.0, 1.0), center=marker_offset, side=MARKER_SIDE, marker_id=7, radius=140
    )
    cams = {}
    for i, c in enumerate(scene.cameras):
        cams[f"frame_{i:05d}.jpg"] = PinholeCamera(
            K=c.K,
            R=c.R,
            t=c.t,
            dist=None if dist is None else np.asarray(dist, float),
            image_size=c.image_size,
        )
    imgs = {}
    for i, c in enumerate(cams.values()):
        # render the marker *through* the (possibly distorted) camera model
        imgs[f"frame_{i:05d}.jpg"] = synthetic.render_marker_view(
            c, scene.corners3d, scene.marker_img, scene.image_size
        )
    return cams, imgs


def _stoma_on_skin_with_junk():
    """Skin slab z∈[-6,0] (radius 60) + stoma cylinder r=16.5 z∈[0,15] at the origin,
    plus a big 'table' slab far below and a junk blob off to the side — the kind of
    background OpenMVS reconstructs. The marker sits at x=70 on the skin plane."""
    skin = trimesh.creation.cylinder(radius=60.0, height=6.0, sections=64)
    skin.apply_translation([0, 0, -3.0])
    stoma = trimesh.creation.cylinder(radius=R_STOMA, height=15.0, sections=96)
    stoma.apply_translation([0, 0, 7.5])
    table = trimesh.creation.box(extents=[400, 400, 4])
    table.apply_translation([0, 0, -40])
    junk = trimesh.creation.icosphere(subdivisions=2, radius=25.0)
    junk.apply_translation([160, 120, 10])
    mesh = trimesh.util.concatenate([skin, stoma, table, junk])
    return np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int)


def test_measure_scan_recovers_diameter_with_ground_truth():
    cams, imgs = _scene()
    verts, faces = _stoma_on_skin_with_junk()
    res = measure_scan(
        verts,
        faces,
        cams,
        imgs,
        marker_side_mm=MARKER_SIDE,
        marker_id=7,
        grace_ring_mm=3.0,
        truth_mm=33.0,
        engine="colmap+openmvs",
    )
    assert res.marker_views >= 8
    assert math.isclose(res.scale_mm_per_unit, 1.0, abs_tol=0.01)
    assert res.diameter_mm == pytest.approx(33.0, abs=0.3)
    assert res.within_tolerance is True
    assert res.orientation_method in ("aruco+ransac", "aruco")
    assert 0.5 < res.extra["slice_height_mm_above_skin"] < 5.0

    rj = res.result_json()
    assert rj["diameter_mm"] == pytest.approx(33.0, abs=0.3)
    assert len(rj["outline_mm"]) == 100
    assert len(rj["wafer_outline_mm"]) == 100
    assert "gcode" not in rj  # G-code is an object, never in the poll payload
    assert rj["shape"]["max_width_mm"] == pytest.approx(33.0, abs=0.3)
    assert rj["shape"]["min_width_mm"] == pytest.approx(33.0, abs=0.3)  # a cylinder
    assert rj["wafer_shape"]["max_width_mm"] == pytest.approx(39.0, abs=0.3)
    assert len(rj["shape"]["widths_by_angle"]) == 36
    assert rj["clearance_mm"]["passes"] is True

    # the wafer G-code is the grace ring in mm, in the GRBL dialect
    assert res.gcode_dialect == "grbl"
    xy = [p for p in gcode.parse_xy(res.gcode) if p != (0.0, 0.0)]
    radii = [math.hypot(x, y) for x, y in xy]
    assert min(radii) == pytest.approx(19.5, abs=0.3)
    assert max(radii) == pytest.approx(19.5, abs=0.3)


def test_lens_distortion_does_not_bias_scale():
    """Strong barrel distortion (k1=-0.25) on every view: without undistortion the
    triangulated marker shrinks/grows and scale drifts by >1%."""
    cams, imgs = _scene(dist=[-0.25, 0.08, 0.0, 0.0])
    verts, faces = _stoma_on_skin_with_junk()
    res = measure_scan(
        verts, faces, cams, imgs, marker_side_mm=MARKER_SIDE, marker_id=7, truth_mm=33.0
    )
    assert math.isclose(res.scale_mm_per_unit, 1.0, abs_tol=0.01)
    assert res.diameter_mm == pytest.approx(33.0, abs=0.4)


def test_measure_scan_needs_the_marker():
    cams, imgs = _scene()
    verts, faces = _stoma_on_skin_with_junk()
    blank = {name: np.full((800, 800), 255, np.uint8) for name in imgs}  # no marker
    with pytest.raises(MarkerNotFound) as ei:
        measure_scan(verts, faces, cams, blank, marker_side_mm=MARKER_SIDE, marker_id=7)
    assert "square card" in ei.value.user_message  # patient-safe text, no jargon


def test_empty_roi_is_a_measure_error():
    cams, imgs = _scene(marker_offset=(500.0, 0.0, 0.0))  # marker far from any mesh
    verts, faces = _stoma_on_skin_with_junk()
    with pytest.raises(MeasureError):
        measure_scan(verts, faces, cams, imgs, marker_side_mm=MARKER_SIDE, marker_id=7)


def test_params_round_trip_through_job_config():
    cfg = {
        "grace_ring_mm": 2.5,
        "marker_side_mm": 50,
        "gcode_dialect": "stoma-plotter",
        "slice_margin_mm": 2.0,
        "truth_mm": "33.2",
        "unrelated": 1,
    }
    p = MeasureParams.from_config(cfg)
    assert p.grace_ring_mm == 2.5 and p.marker_side_mm == 50.0
    assert p.gcode_dialect == "stoma-plotter"
    assert p.slice.margin_mm == 2.0
    assert p.truth_mm == 33.2
    back = p.to_config()
    assert back["slice_margin_mm"] == 2.0 and back["grace_ring_mm"] == 2.5


def test_axis_prefers_the_object_next_to_the_card():
    """A bigger object 100 mm away (table edge, the patient's body) must not be
    mistaken for the stoma: the axis is the plausible cluster nearest the card."""
    cams, imgs = _scene()
    verts, faces = _stoma_on_skin_with_junk()
    big = trimesh.creation.cylinder(radius=30.0, height=25.0, sections=96)
    big.apply_translation([-95.0, 40.0, 12.5])  # far from the card at x=+70
    mesh = trimesh.util.concatenate([trimesh.Trimesh(verts, faces, process=False), big])
    res = measure_scan(
        np.asarray(mesh.vertices, float),
        np.asarray(mesh.faces, int),
        cams,
        imgs,
        marker_side_mm=MARKER_SIDE,
        marker_id=7,
        truth_mm=33.0,
    )
    assert res.diameter_mm == pytest.approx(33.0, abs=0.4)
    assert res.extra["axis_to_card_mm"] < 90


def test_point_cloud_mode_measures_without_a_mesh():
    """Dense point cloud in, no faces: polar sections only. Same 33 mm answer."""
    cams, imgs = _scene()
    verts, faces = _stoma_on_skin_with_junk()
    mesh = trimesh.Trimesh(verts, faces, process=False)
    pts, _ = trimesh.sample.sample_surface(
        mesh, 1_500_000, seed=0
    )  # ~5 pts/mm², like a real dense cloud
    res = measure_scan(
        np.asarray(pts, float),
        np.zeros((0, 3), int),
        cams,
        imgs,
        marker_side_mm=MARKER_SIDE,
        marker_id=7,
        truth_mm=33.0,
    )
    assert res.extra["input_kind"] == "point-cloud"
    assert res.extra["outline_method"] == "polar-cloud"
    assert res.diameter_mm == pytest.approx(33.0, abs=0.4)
    assert res.shape["min_width_mm"] == pytest.approx(33.0, abs=0.5)
    assert len(res.outline_mm) == 100 and res.clearance["passes"]
