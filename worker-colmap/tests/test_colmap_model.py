"""P1-10 COLMAP model parsing → PinholeCameras."""

from __future__ import annotations

import numpy as np

import colmap_model as cm  # noqa: E402 — worker module (conftest adds the path)
from app.measure.orientation import PinholeCamera


def test_quat_to_rot_identity():
    assert np.allclose(cm._quat_to_rot(1, 0, 0, 0), np.eye(3))


def test_quat_to_rot_is_orthonormal():
    R = cm._quat_to_rot(0.5, 0.5, -0.5, 0.5)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-9)
    assert np.isclose(np.linalg.det(R), 1.0)


def test_parse_model_round_trips_projection():
    # a known pose + intrinsics
    qw, qx, qy, qz = 0.5, 0.5, -0.5, 0.5
    t = [0.1, -0.2, 3.0]
    fx = fy = 900.0
    cx, cy = 640.0, 360.0

    cameras_txt = f"# comment\n1 PINHOLE 1280 720 {fx} {fy} {cx} {cy}\n"
    images_txt = (
        "# Image list\n"
        f"7 {qw} {qx} {qy} {qz} {t[0]} {t[1]} {t[2]} 1 frame_00007.jpg\n"
        "100.0 200.0 -1 300.0 400.0 -1\n"  # points2D line (ignored)
    )

    cams = cm.parse_model(cameras_txt, images_txt)
    assert set(cams) == {"frame_00007.jpg"}
    cam = cams["frame_00007.jpg"]

    # reference camera built directly from the same fields
    ref = PinholeCamera(
        K=np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]]),
        R=cm._quat_to_rot(qw, qx, qy, qz),
        t=np.array(t),
    )
    pt = np.array([[0.3, -0.1, 0.05]])
    assert np.allclose(cam.project(pt), ref.project(pt), atol=1e-9)


def test_simple_radial_intrinsics():
    cams = cm.parse_model(
        "1 SIMPLE_RADIAL 800 600 750 400 300 0.01\n",
        "1 1 0 0 0 0 0 1 1 a.jpg\n0 0 -1\n",
    )
    cam = cams["a.jpg"]
    K = cam.K
    assert K[0, 0] == 750 and K[1, 1] == 750  # f
    assert K[0, 2] == 400 and K[1, 2] == 300  # cx, cy
    assert cam.dist is not None and cam.dist[0] == 0.01  # k carried through
    assert cam.image_size == (800, 600)


def test_opencv_model_carries_tangential_terms():
    cams = cm.parse_model(
        "1 OPENCV 800 600 750 740 400 300 -0.1 0.02 0.001 -0.002\n",
        "1 1 0 0 0 0 0 1 1 a.jpg\n0 0 -1\n",
    )
    d = cams["a.jpg"].dist
    assert np.allclose(d, [-0.1, 0.02, 0.001, -0.002, 0.0])


def test_pinhole_has_no_distortion():
    cams = cm.parse_model("1 PINHOLE 8 6 5 5 4 3\n", "1 1 0 0 0 0 0 1 1 a.jpg\n0 0 -1\n")
    assert cams["a.jpg"].dist is None
