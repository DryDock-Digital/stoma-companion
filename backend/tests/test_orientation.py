"""P2-2/P2-3 orientation. Pure-geometry tests (camera/triangulate/plane fit/PCA/
RANSAC) run always; the detection + synthetic-render path runs when OpenCV is
present. Everything is scored against known ground-truth normals — no capture."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.measure import orientation as ori


def _normalize(v):
    return np.asarray(v, float) / np.linalg.norm(v)


# --- pure geometry (P2-2) --------------------------------------------------


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
    assert ori.triangulate(cams, obs)[0] == pytest.approx(p[0], abs=1e-6)


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
    normal = _normalize([math.sin(math.radians(25)), 0, math.cos(math.radians(25))])
    u = _normalize(np.cross([0, 1, 0], normal))
    v = np.cross(normal, u)
    c = np.array([2.0, 1.0, 0.0])
    corners = np.array([c - u - v, c + u - v, c + u + v, c - u + v])
    cams = [
        ori.PinholeCamera.look_at(c + normal * 100 + u * 30, c, image_size=(800, 800)),
        ori.PinholeCamera.look_at(c + normal * 100 - u * 30, c, image_size=(800, 800)),
        ori.PinholeCamera.look_at(c + normal * 100 + v * 30, c, image_size=(800, 800)),
    ]
    obs = [cam.project(corners) for cam in cams]
    plane = ori.recover_marker_plane(cams, obs)
    assert ori.angle_between_axes_deg(plane.normal, normal) < 1e-4
    assert float(plane.normal @ normal) > 0


# --- PCA / RANSAC plane fits (P2-3) ----------------------------------------


def _planar_cloud(n=1000, noise=0.1, seed=0):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(-20, 20, (n, 2))
    z = noise * rng.standard_normal(n)
    return np.column_stack([xy, z])


def test_pca_normal_of_clean_plane():
    normal = ori.pca_plane_normal(_planar_cloud(), orient_toward=np.array([0, 0, 100]))
    assert ori.angle_between_axes_deg(normal, [0, 0, 1]) < 0.5
    assert normal[2] > 0  # oriented toward the hint


def test_ransac_rejects_outliers_and_beats_pca():
    # a flat plane plus a one-sided raised outlier cluster (reconstruction junk)
    rng = np.random.default_rng(1)
    plane = _planar_cloud(1000, noise=0.1, seed=2)
    k = 150
    cluster = np.column_stack(
        [rng.uniform(14, 20, k), rng.uniform(-20, 20, k), rng.uniform(5, 10, k)]
    )
    pts = np.vstack([plane, cluster])

    pca_err = ori.angle_between_axes_deg(ori.pca_plane_normal(pts), [0, 0, 1])
    res = ori.ransac_plane_normal(pts, threshold=0.5, seed=0)
    ransac_err = ori.angle_between_axes_deg(res.normal, [0, 0, 1])

    assert pca_err > 2.0  # least-squares is dragged by the cluster
    assert ransac_err < 0.5  # robust fit ignores it
    assert res.inlier_fraction < 0.95  # the cluster was excluded


# --- detection + synthetic render (needs OpenCV) ---------------------------

cv2 = pytest.importorskip("cv2")

from app.verify import synthetic  # noqa: E402
from app.verify.orientation import (  # noqa: E402
    ORIENTATION_METHODS,
    compare,
    default_suite,
    recommended_chain,
    score_scene,
)


def test_aruco_method_recovers_tilted_normal():
    normal = _normalize([math.sin(math.radians(20)), 0, math.cos(math.radians(20))])
    scene = synthetic.build_scene("tilt20", normal, with_skin=True)
    run = score_scene(scene, ORIENTATION_METHODS["aruco"], tolerance_deg=2.0)
    assert run.error is None and run.error_deg < 0.5 and run.passed


def test_all_methods_score_and_chain_prefers_aruco():
    boards = compare(default_suite(), tolerance_deg=2.0)
    assert set(boards) == {"aruco", "ransac", "pca"}
    # marker + robust skin fit both nail it across the suite
    assert boards["aruco"].all_passed
    assert boards["ransac"].all_passed
    # primary is the marker; skin fits are fallbacks
    chain = recommended_chain(boards)
    assert chain[0] == "aruco"
    assert chain[-1] == "pca"
    assert boards["aruco"].summary()["max_error_deg"] < 1.0


# --- robust triangulation + distortion (review fix) ---------------------------


def test_undistort_inverts_distort():
    from app.measure.orientation import distort_normalized, undistort_normalized

    x = np.linspace(-0.4, 0.4, 9)
    y = np.linspace(-0.3, 0.3, 9)
    d = np.array([-0.2, 0.05, 0.001, -0.002, 0.01])
    xd, yd = distort_normalized(x, y, d)
    xu, yu = undistort_normalized(xd, yd, d)
    assert np.allclose(xu, x, atol=1e-9) and np.allclose(yu, y, atol=1e-9)


def test_robust_triangulation_drops_a_bad_view():
    from app.measure.orientation import PinholeCamera, triangulate_robust

    pts = np.array([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [20.0, 20.0, 0.0], [0.0, 20.0, 0.0]])
    cams = [
        PinholeCamera.look_at(
            (80 * np.cos(a), 80 * np.sin(a), 90.0), (10, 10, 0), image_size=(800, 800)
        )
        for a in np.linspace(0, 2 * np.pi, 8, endpoint=False)
    ]
    obs = [c.project(pts) for c in cams]
    obs[3] = obs[3] + np.array([40.0, -25.0])  # a mis-detection / motion-blurred frame
    tri = triangulate_robust(cams, obs, reproj_threshold_px=1.0)
    assert not tri.inlier_mask[3] and tri.inlier_mask.sum() == 7
    assert np.allclose(tri.points, pts, atol=1e-3)


def test_skin_refinement_keeps_marker_when_skin_disagrees():
    from app.measure.orientation import refine_up_axis_with_skin

    rng = np.random.default_rng(1)
    # skin tilted 25° from the marker → beyond max_deviation → marker wins
    tilt = np.deg2rad(25)
    u = np.array([np.cos(tilt), 0, -np.sin(tilt)])
    v = np.array([0, 1.0, 0])
    pts = rng.uniform(-40, 40, (400, 1)) * u + rng.uniform(-40, 40, (400, 1)) * v
    choice = refine_up_axis_with_skin(
        [0, 0, 1.0], [0, 0, 0], pts, orient_toward=[0, 0, 100.0], max_deviation_deg=15
    )
    assert choice.method == "aruco"
    assert np.allclose(choice.normal, [0, 0, 1])
    # 5° off → refined to the skin
    tilt = np.deg2rad(5)
    u = np.array([np.cos(tilt), 0, -np.sin(tilt)])
    pts = rng.uniform(-40, 40, (400, 1)) * u + rng.uniform(-40, 40, (400, 1)) * v
    choice = refine_up_axis_with_skin(
        [0, 0, 1.0], [0, 0, 0], pts, orient_toward=[0, 0, 100.0], max_deviation_deg=15
    )
    assert choice.method == "aruco+ransac"
    assert (
        abs(np.degrees(np.arccos(abs(choice.normal @ np.array([np.sin(tilt), 0, np.cos(tilt)])))))
        < 0.5
    )


def test_plane_fit_on_large_point_cloud_is_thin_svd():
    """A 300k-point skin patch must not allocate an N×N matrix (it OOM-killed the
    first real measurement)."""
    from app.measure.orientation import fit_plane_normal, refine_up_axis_with_skin

    rng = np.random.default_rng(0)
    pts = np.column_stack(
        [rng.uniform(-50, 50, 300_000), rng.uniform(-50, 50, 300_000), rng.normal(0, 0.1, 300_000)]
    )
    n, _, _ = fit_plane_normal(pts)
    assert abs(abs(n[2]) - 1) < 1e-3
    ch = refine_up_axis_with_skin([0, 0, 1.0], [0, 0, 0], pts, orient_toward=[0, 0, 100.0])
    assert ch.method == "aruco+ransac"
