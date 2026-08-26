"""P2-2 marker-plane orientation. Pure-geometry tests (camera/triangulate/plane
fit) run always; the detection + synthetic-render path runs when OpenCV is present.
Scored against known ground-truth normals — no physical capture."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.measure import orientation as ori


def _normalize(v):
    return np.asarray(v, float) / np.linalg.norm(v)


# --- pure geometry ---------------------------------------------------------


def test_project_center_lands_at_principal_point():
    cam = ori.PinholeCamera.look_at((0, 0, 100), (0, 0, 0), image_size=(800, 800))
    px = cam.project(np.array([[0.0, 0.0, 0.0]]))[0]
    assert px == pytest.approx([400.0, 400.0], abs=1e-6)
    assert cam.center == pytest.approx([0, 0, 100], abs=1e-9)


def test_triangulate_recovers_known_point():
    p = np.array([[5.0, -3.0, 2.0]])
    cams = [
        ori.PinholeCamera.look_at((0, 0, 120), (0, 0, 0), image_size=(800, 800)),
        ori.PinholeCamera.look_at((110, 0, 60), (0, 0, 0), image_size=(800, 800)),
        ori.PinholeCamera.look_at((0, 100, 70), (0, 0, 0), image_size=(800, 800)),
    ]
    obs = [cam.project(p) for cam in cams]
    out = ori.triangulate(cams, obs)
    assert out[0] == pytest.approx(p[0], abs=1e-6)


def test_fit_plane_normal_of_z0_plane():
    pts = np.array([[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], dtype=float)
    normal, centroid, rms = ori.fit_plane_normal(pts)
    assert abs(abs(normal[2]) - 1.0) < 1e-9
    assert rms < 1e-9
    assert centroid == pytest.approx([0, 0, 0], abs=1e-9)


def test_angle_between_axes_is_sign_invariant():
    assert ori.angle_between_axes_deg([0, 0, 1], [0, 0, 1]) == pytest.approx(0.0, abs=1e-9)
    assert ori.angle_between_axes_deg([0, 0, 1], [0, 0, -1]) == pytest.approx(0.0, abs=1e-9)
    assert ori.angle_between_axes_deg([0, 0, 1], [1, 0, 0]) == pytest.approx(90.0, abs=1e-9)


def test_recover_plane_from_ground_truth_projections():
    # a tilted marker plane, 4 coplanar corners, projected exactly (no detection)
    normal = _normalize([math.sin(math.radians(25)), 0, math.cos(math.radians(25))])
    u = _normalize(np.cross([0, 1, 0], normal))
    v = np.cross(normal, u)
    c = np.array([2.0, 1.0, 0.0])
    corners = np.array([c - u - v, c + u - v, c + u + v, c - u + v]) * 1.0
    cams = [
        ori.PinholeCamera.look_at(c + normal * 100 + u * 30, c, image_size=(800, 800)),
        ori.PinholeCamera.look_at(c + normal * 100 - u * 30, c, image_size=(800, 800)),
        ori.PinholeCamera.look_at(c + normal * 100 + v * 30, c, image_size=(800, 800)),
    ]
    obs = [cam.project(corners) for cam in cams]
    plane = ori.recover_marker_plane(cams, obs)
    assert ori.angle_between_axes_deg(plane.normal, normal) < 1e-4
    # normal oriented toward the cameras (positive component along true up)
    assert float(plane.normal @ normal) > 0


# --- detection + synthetic render (needs OpenCV) ---------------------------

cv2 = pytest.importorskip("cv2")

from app.verify import synthetic  # noqa: E402
from app.verify.orientation import default_suite, score_scene, score_suite  # noqa: E402


def test_scene_recovers_tilted_normal_via_detection():
    normal = _normalize([math.sin(math.radians(20)), 0, math.cos(math.radians(20))])
    scene = synthetic.build_scene("tilt20", normal)
    run = score_scene(scene, tolerance_deg=2.0)
    assert run.error is None
    assert run.views_detected >= 8
    assert run.error_deg < 0.5  # synthetic recovery is sub-degree
    assert run.passed


def test_default_suite_all_within_tolerance():
    board = score_suite(default_suite(), tolerance_deg=2.0)
    assert board.all_passed
    s = board.summary()
    assert s["scenes"] == 6
    assert s["max_error_deg"] < 1.0
    csv = board.to_csv()
    assert csv.splitlines()[0].startswith("scene,views_detected,error_deg")
