"""Marker-plane orientation (P2-2).

The ArUco marker sits on the peristomal skin plane, so its plane normal is the
slice "up" axis (FR-04). Given the marker seen in several views with known camera
poses, we triangulate its corners into 3-D and fit a plane — the normal is "up".

This is the geometry half (pure numpy): a pinhole camera, linear (DLT)
triangulation, and an SVD plane fit. Detection (cv2.aruco) and the synthetic
renderer live in `app.verify.synthetic`; scoring in `app.verify.orientation`.

In the real pipeline the same recovery runs on COLMAP camera poses (or the legacy
mesh-orbit views); here it's exercised against synthetic scenes with known truth.
Real-footage validation is deferred with the rest of the fixture work (P0-3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


@dataclass
class PinholeCamera:
    """OpenCV-convention pinhole camera: x right, y down, z forward.

    K: 3×3 intrinsics. R: 3×3 world→camera rotation (rows are the camera axes in
    world coords). t: translation such that X_cam = R·X_world + t.
    """

    K: np.ndarray
    R: np.ndarray
    t: np.ndarray

    @classmethod
    def look_at(
        cls,
        eye,
        target,
        *,
        image_size: tuple[int, int],
        fov_deg: float = 55.0,
        up=(0.0, 0.0, 1.0),
    ) -> PinholeCamera:
        eye = np.asarray(eye, dtype=float)
        target = np.asarray(target, dtype=float)
        up = np.asarray(up, dtype=float)

        z = _normalize(target - eye)  # forward
        x = _normalize(np.cross(z, up))  # right
        if not np.isfinite(x).all() or np.linalg.norm(x) < 1e-9:
            x = _normalize(np.cross(z, np.array([0.0, 1.0, 0.0])))
        y = np.cross(z, x)  # down
        R = np.vstack([x, y, z])
        t = -R @ eye

        w, h = image_size
        f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
        K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
        return cls(K=K, R=R, t=t)

    @property
    def projection_matrix(self) -> np.ndarray:
        return self.K @ np.hstack([self.R, self.t.reshape(3, 1)])

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t

    def project(self, points: np.ndarray) -> np.ndarray:
        """World points (N,3) → pixels (N,2)."""
        pts = np.atleast_2d(np.asarray(points, dtype=float))
        cam = pts @ self.R.T + self.t
        proj = cam @ self.K.T
        return proj[:, :2] / proj[:, 2:3]


def triangulate(cameras: list[PinholeCamera], observations: list[np.ndarray]) -> np.ndarray:
    """DLT triangulation. `observations[i]` is an (N,2) pixel array for camera i;
    all cameras observe the same N points. Returns (N,3) world points."""
    projs = [cam.projection_matrix for cam in cameras]
    n_points = observations[0].shape[0]
    out = np.empty((n_points, 3))
    for j in range(n_points):
        rows = []
        for pmat, obs in zip(projs, observations, strict=True):
            u, v = obs[j]
            rows.append(u * pmat[2] - pmat[0])
            rows.append(v * pmat[2] - pmat[1])
        _, _, vt = np.linalg.svd(np.asarray(rows))
        x = vt[-1]
        out[j] = x[:3] / x[3]
    return out


def fit_plane_normal(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Least-squares plane through >=3 points. Returns (unit normal, centroid,
    RMS out-of-plane distance)."""
    pts = np.asarray(points, dtype=float)
    centroid = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centroid)
    normal = _normalize(vt[-1])
    rms = float(np.sqrt(np.mean(((pts - centroid) @ normal) ** 2)))
    return normal, centroid, rms


@dataclass
class MarkerPlane:
    normal: np.ndarray  # unit "up" axis, oriented toward the cameras
    centroid: np.ndarray
    corners: np.ndarray  # (4,3) triangulated marker corners
    rms_planarity: float


def recover_marker_plane(
    cameras: list[PinholeCamera],
    corner_observations: list[np.ndarray],
    orient_toward: np.ndarray | None = None,
) -> MarkerPlane:
    """Triangulate the marker's 4 corners across views and fit their plane.

    `corner_observations[i]` is the (4,2) detected corners in camera i (same
    physical-corner order across views — cv2.aruco guarantees this). `orient_toward`
    (default: mean camera center) flips the normal to point at the viewers, since a
    plane normal is otherwise sign-ambiguous.
    """
    if len(cameras) < 2:
        raise ValueError("need >= 2 views to triangulate")
    corners = triangulate(cameras, corner_observations)
    normal, centroid, rms = fit_plane_normal(corners)

    if orient_toward is None:
        orient_toward = np.mean([cam.center for cam in cameras], axis=0)
    if float(normal @ (np.asarray(orient_toward, dtype=float) - centroid)) < 0:
        normal = -normal

    return MarkerPlane(normal=normal, centroid=centroid, corners=corners, rms_planarity=rms)


def angle_between_axes_deg(a: np.ndarray, b: np.ndarray) -> float:
    """Unsigned angle between two directions treated as axes (±ambiguous), in
    degrees. This is the orientation error that matters for slicing: the slice
    plane is identical for n and −n."""
    a, b = _normalize(np.asarray(a, dtype=float)), _normalize(np.asarray(b, dtype=float))
    return math.degrees(math.acos(min(1.0, abs(float(a @ b)))))


def _orient(normal: np.ndarray, toward: np.ndarray | None, centroid: np.ndarray) -> np.ndarray:
    if toward is None:
        return normal
    if float(normal @ (np.asarray(toward, dtype=float) - centroid)) < 0:
        return -normal
    return normal


# --- marker-independent fallbacks (P2-3) -----------------------------------


def pca_plane_normal(points: np.ndarray, orient_toward: np.ndarray | None = None) -> np.ndarray:
    """Least-squares (PCA) plane normal over *all* points — simple but biased by
    non-planar features (a stoma bump) and outliers. The non-robust fallback."""
    normal, centroid, _ = fit_plane_normal(points)
    return _orient(normal, orient_toward, centroid)


@dataclass
class RansacPlane:
    normal: np.ndarray
    centroid: np.ndarray
    inlier_mask: np.ndarray
    inlier_fraction: float


def ransac_plane_normal(
    points: np.ndarray,
    *,
    threshold: float,
    iterations: int = 300,
    seed: int = 0,
    orient_toward: np.ndarray | None = None,
) -> RansacPlane:
    """Robust plane fit: the peristomal skin is planar, but the stoma bump and
    reconstruction outliers are not. Sample 3 points, score inliers within
    `threshold`, keep the best consensus, then refit on its inliers. Deterministic
    for a given `seed`."""
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n < 3:
        raise ValueError("need >= 3 points for RANSAC")
    rng = np.random.default_rng(seed)

    best_mask = None
    best_count = 0
    for _ in range(iterations):
        idx = rng.choice(n, size=3, replace=False)
        a, b, c = pts[idx]
        normal = np.cross(b - a, c - a)
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal = normal / norm
        dist = np.abs((pts - a) @ normal)
        mask = dist < threshold
        count = int(mask.sum())
        if count > best_count:
            best_count, best_mask = count, mask

    if best_mask is None or best_count < 3:
        # no consensus — fall back to plain PCA over everything
        normal, centroid, _ = fit_plane_normal(pts)
        normal = _orient(normal, orient_toward, centroid)
        return RansacPlane(normal, centroid, np.ones(n, bool), 1.0)

    normal, centroid, _ = fit_plane_normal(pts[best_mask])
    normal = _orient(normal, orient_toward, centroid)
    return RansacPlane(normal, centroid, best_mask, best_count / n)
