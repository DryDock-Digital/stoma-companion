"""P1-10 end-to-end measurement — validated with ground truth: a known-size ArUco
marker (for scale) + a cylinder of known 33 mm diameter, viewed from known poses.
Proves the measurement half of the chain; the reconstruction half is COLMAP on the
worker."""

from __future__ import annotations

import math

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
cv2 = pytest.importorskip("cv2")


def _scene_and_mesh():
    from app.verify import synthetic

    # marker 40 units on the z=0 plane; cameras orbit above (+z "up").
    scene = synthetic.build_scene("flat", (0.0, 0.0, 1.0), side=40.0, marker_id=7)
    cams = {f"frame_{i:05d}.jpg": c for i, c in enumerate(scene.cameras)}
    imgs = {f"frame_{i:05d}.jpg": v for i, v in enumerate(scene.render_views())}
    # a cylinder radius 16.5 units (→ 33 mm at scale 1.0), axis = z = marker normal
    cyl = trimesh.creation.cylinder(radius=16.5, height=20.0, sections=64)
    return cams, imgs, np.asarray(cyl.vertices, float), np.asarray(cyl.faces, int)


def test_measure_scan_recovers_diameter_with_ground_truth():
    from app.measure.measure_scan import measure_scan

    cams, imgs, verts, faces = _scene_and_mesh()
    res = measure_scan(
        verts,
        faces,
        cams,
        imgs,
        marker_side_mm=40.0,  # marker is 40 units → scale ≈ 1.0 mm/unit
        marker_id=7,
        grace_ring_mm=3.0,
        truth_mm=33.0,
        engine="colmap+openmvs",
    )

    assert res.marker_views >= 8
    assert math.isclose(res.scale_mm_per_unit, 1.0, abs_tol=0.02)
    assert res.diameter_mm == pytest.approx(33.0, abs=0.6)
    assert res.within_tolerance is True

    rj = res.result_json()
    assert rj["diameter_mm"] == pytest.approx(33.0, abs=0.6)
    assert len(rj["outline_mm"]) == 100
    assert len(rj["wafer_outline_mm"]) == len(rj["outline_mm"])
    assert res.gcode.startswith("; Base perimeter")


def test_measure_scan_needs_the_marker():
    from app.measure.measure_scan import MeasureError, measure_scan

    cams, imgs, verts, faces = _scene_and_mesh()
    blank = {name: np.full((400, 400), 255, np.uint8) for name in imgs}  # no marker
    with pytest.raises(MeasureError):
        measure_scan(verts, faces, cams, blank, marker_side_mm=40.0, marker_id=7)
