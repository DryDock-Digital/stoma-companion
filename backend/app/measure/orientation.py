"""Marker-plane orientation (P2-2) + camera model + robust triangulation.

The ArUco marker sits on the peristomal skin plane, so its plane normal is the
slice "up" axis (FR-04). Given the marker seen in several views with known camera
poses, we triangulate its corners into 3-D and fit a plane — the normal is "up".

This is the geometry half (pure numpy): a pinhole camera *with lens distortion*,
linear (DLT) triangulation with per-view weights and reprojection-based outlier
rejection, and an SVD plane fit. Detection (cv2.aruco) and the synthetic renderer
live in `app.verify.synthetic`; scoring in `app.verify.orientation`.

Engine-agnostic by construction: cameras arrive as `PinholeCamera` (see
`poses.py` for the neutral on-disk form every reconstruction engine must emit).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


@dataclass
class PinholeCamera:
    """OpenCV-convention pinhole camera: x right, y down, z forward.

    K: 3×3 intrinsics. R: 3×3 world→camera rotation (rows are the camera axes in
    world coords). t: translation such that X_cam = R·X_world + t.
    dist: OpenCV distortion coefficients (k1, k2, p1, p2[, k3]) or None for an
    ideal pinhole. `undistort` removes them from pixel observations before
    triangulation — phone wide lenses put several px of radial error at the
    marker corners, enough to bias scale by ~1% (≈0.3–0.5 mm on a 40 mm base).
    """

    K: np.ndarray
    R: np.ndarray
    t: np.ndarray
    dist: np.ndarray | None = None
    image_size: tuple[int, int] | None = None  # (width, height), informational
    meta: dict = field(default_factory=dict)

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
        return cls(K=K, R=R, t=t, image_size=(w, h))

    @property
    def projection_matrix(self) -> np.ndarray:
        return self.K @ np.hstack([self.R, self.t.reshape(3, 1)])

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t

    @property
    def has_distortion(self) -> bool:
        return self.dist is not None and bool(np.any(np.asarray(self.dist) != 0))

    def project(self, points: np.ndarray) -> np.ndarray:
        """World points (N,3) → pixels (N,2), applying distortion if present."""
        pts = np.atleast_2d(np.asarray(points, dtype=float))
        cam = pts @ self.R.T + self.t
        xn = cam[:, 0] / cam[:, 2]
        yn = cam[:, 1] / cam[:, 2]
        if self.has_distortion:
            xn, yn = distort_normalized(xn, yn, np.asarray(self.dist, dtype=float))
        u = self.K[0, 0] * xn + self.K[0, 1] * yn + self.K[0, 2]
        v = self.K[1, 1] * yn + self.K[1, 2]
        return np.column_stack([u, v])

    def undistort(self, pixels: np.ndarray) -> np.ndarray:
        """Distorted pixel observations (N,2) → ideal pinhole pixels (N,2)."""
        px = np.atleast_2d(np.asarray(pixels, dtype=float))
        if not self.has_distortion:
            return px
        fx, fy = self.K[0, 0], self.K[1, 1]
        cx, cy = self.K[0, 2], self.K[1, 2]
        skew = self.K[0, 1]
        yd = (px[:, 1] - cy) / fy
        xd = (px[:, 0] - cx - skew * yd) / fx
        xn, yn = undistort_normalized(xd, yd, np.asarray(self.dist, dtype=float))
        return np.column_stack([fx * xn + skew * yn + cx, fy * yn + cy])


# --- OpenCV Brown–Conrady distortion (k1 k2 p1 p2 [k3]) -----------------------


def distort_normalized(x: np.ndarray, y: np.ndarray, d: np.ndarray):
    k1, k2, p1, p2 = (list(d) + [0.0] * 4)[:4]
    k3 = d[4] if len(d) > 4 else 0.0
    r2 = x * x + y * y
    radial = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return xd, yd


def undistort_normalized(xd: np.ndarray, yd: np.ndarray, d: np.ndarray, iterations: int = 20):
    """Invert `distort_normalized` by fixed-point iteration (what cv2.undistortPoints
    does). Converges in a handful of steps for phone-lens distortion magnitudes."""
    k1, k2, p1, p2 = (list(d) + [0.0] * 4)[:4]
    k3 = d[4] if len(d) > 4 else 0.0
    x, y = xd.copy(), yd.copy()
    for _ in range(iterations):
        r2 = x * x + y * y
        radial = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        x = (xd - dx) / radial
        y = (yd - dy) / radial
    return x, y


# --- triangulation ---------------------------------------------------------


def triangulate(
    cameras: list[PinholeCamera],
    observations: list[np.ndarray],
    weights: list[float] | np.ndarray | None = None,
    *,
    undistort: bool = True,
) -> np.ndarray:
    """(Weighted) DLT triangulation. `observations[i]` is an (N,2) pixel array for
    camera i; all cameras observe the same N points. Returns (N,3) world points.
    Observations are undistorted with each camera's coefficients first."""
    projs = [cam.projection_matrix for cam in cameras]
    obs = [
        cam.undistort(o) if undistort else np.asarray(o, float)
        for cam, o in zip(cameras, observations, strict=True)
    ]
    w = np.ones(len(cameras)) if weights is None else np.asarray(weights, float)
    n_points = obs[0].shape[0]
    out = np.empty((n_points, 3))
    for j in range(n_points):
        rows = []
        for pmat, o, wi in zip(projs, obs, w, strict=True):
            u, v = o[j]
            rows.append(wi * (u * pmat[2] - pmat[0]))
            rows.append(wi * (v * pmat[2] - pmat[1]))
        _, _, vt = np.linalg.svd(np.asarray(rows))
        x = vt[-1]
        out[j] = x[:3] / x[3]
    return out


@dataclass
class RobustTriangulation:
    points: np.ndarray  # (N,3)
    inlier_mask: np.ndarray  # (V,) bool per view
    reprojection_px: np.ndarray  # (V,) mean reprojection error per view (inliers used)


def triangulate_robust(
    cameras: list[PinholeCamera],
    observations: list[np.ndarray],
    weights: list[float] | np.ndarray | None = None,
    *,
    reproj_threshold_px: float = 2.0,
    rounds: int = 3,
    min_views: int = 2,
) -> RobustTriangulation:
    """Outlier-tolerant multi-view triangulation.

    A single bad view (blurred frame, mis-detection, near-tangential look) corrupts
    a joint DLT solve enough that *every* view then shows a similar reprojection
    error, so thresholding the joint solution can't find the culprit. Instead:
    seed with the per-coordinate **median over all view-pair triangulations**
    (LMedS-style), score each view against that seed, keep views within
    max(threshold, 3×median error), then refine by weighted DLT on the inliers
    (repeated `rounds` times). Weights (e.g. √marker pixel area) favour close,
    sharp views."""
    n_views = len(cameras)
    if n_views < min_views:
        raise ValueError(f"need >= {min_views} views to triangulate")
    w_all = np.ones(n_views) if weights is None else np.asarray(weights, float)
    obs = [np.asarray(o, float) for o in observations]

    def errors_for(points: np.ndarray) -> np.ndarray:
        return np.array(
            [
                float(np.mean(np.linalg.norm(cam.project(points) - o, axis=1)))
                for cam, o in zip(cameras, obs, strict=True)
            ]
        )

    # seed: median of pairwise solutions (robust to a minority of bad views)
    if n_views > 2:
        pair_pts = []
        for i in range(n_views):
            for j in range(i + 1, n_views):
                pair_pts.append(triangulate([cameras[i], cameras[j]], [obs[i], obs[j]]))
        seed = np.median(np.stack(pair_pts), axis=0)
    else:
        seed = triangulate(cameras, obs, w_all)

    pts = seed
    mask = np.ones(n_views, dtype=bool)
    errs = errors_for(pts)
    for _ in range(rounds):
        med = float(np.median(errs[mask])) if mask.any() else 0.0
        cutoff = max(reproj_threshold_px, 3.0 * med)
        new_mask = errs <= cutoff
        if new_mask.sum() < min_views:
            break
        mask = new_mask
        idx = np.flatnonzero(mask)
        pts = triangulate([cameras[i] for i in idx], [obs[i] for i in idx], w_all[idx])
        errs = errors_for(pts)
    return RobustTriangulation(points=pts, inlier_mask=mask, reprojection_px=errs)


def fit_plane_normal(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Least-squares plane through >=3 points. Returns (unit normal, centroid,
    RMS out-of-plane distance)."""
    pts = np.asarray(points, dtype=float)
    centroid = pts.mean(axis=0)
    # thin SVD: the full one allocates an N×N U (a 220k-point skin patch → ~380 GB)
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    normal = _normalize(vt[-1])
    rms = float(np.sqrt(np.mean(((pts - centroid) @ normal) ** 2)))
    return normal, centroid, rms


@dataclass
class MarkerPlane:
    normal: np.ndarray  # unit "up" axis, oriented toward the cameras
    centroid: np.ndarray
    corners: np.ndarray  # (4,3) triangulated marker corners
    rms_planarity: float
    views_used: int = 0
    views_total: int = 0
    reprojection_px: float = 0.0  # mean over inlier views


def recover_marker_plane(
    cameras: list[PinholeCamera],
    corner_observations: list[np.ndarray],
    orient_toward: np.ndarray | None = None,
    *,
    weights: list[float] | np.ndarray | None = None,
    reproj_threshold_px: float = 2.0,
) -> MarkerPlane:
    """Triangulate the marker's 4 corners across views (robustly) and fit their plane.

    `corner_observations[i]` is the (4,2) detected corners in camera i (same
    physical-corner order across views — cv2.aruco guarantees this). `orient_toward`
    (default: mean camera center) flips the normal to point at the viewers, since a
    plane normal is otherwise sign-ambiguous.
    """
    if len(cameras) < 2:
        raise ValueError("need >= 2 views to triangulate")
    tri = triangulate_robust(
        cameras, corner_observations, weights, reproj_threshold_px=reproj_threshold_px
    )
    corners = tri.points
    normal, centroid, rms = fit_plane_normal(corners)

    if orient_toward is None:
        used = [cam.center for cam, ok in zip(cameras, tri.inlier_mask, strict=True) if ok]
        orient_toward = np.mean(used, axis=0)
    if float(normal @ (np.asarray(orient_toward, dtype=float) - centroid)) < 0:
        normal = -normal

    return MarkerPlane(
        normal=normal,
        centroid=centroid,
        corners=corners,
        rms_planarity=rms,
        views_used=int(tri.inlier_mask.sum()),
        views_total=len(cameras),
        reprojection_px=float(np.mean(tri.reprojection_px[tri.inlier_mask])),
    )


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


@dataclass
class OrientationChoice:
    """Result of the ArUco → RANSAC-skin refinement (D16)."""

    normal: np.ndarray
    method: str  # 'aruco+ransac' | 'aruco'
    marker_normal: np.ndarray
    skin_normal: np.ndarray | None
    angle_to_marker_deg: float | None
    skin_inlier_fraction: float | None


def refine_up_axis_with_skin(
    marker_normal: np.ndarray,
    marker_centroid: np.ndarray,
    skin_points: np.ndarray,
    *,
    orient_toward: np.ndarray,
    threshold_mm: float = 1.5,
    max_deviation_deg: float = 15.0,
    min_inlier_fraction: float = 0.4,
    min_points: int = 50,
    max_points: int = 20000,
    seed: int = 0,
) -> OrientationChoice:
    """The marker card is flat, but the skin it sits on is not — 20–30 mm from the
    stoma it can be 5–10° off the base plane (≈0.5 mm on the chord). Refine the
    marker normal with a RANSAC fit of the peristomal-skin surface around the stoma
    (D16). The marker stays the reference: if the skin fit disagrees by more than
    `max_deviation_deg`, or has weak consensus, keep the marker normal."""
    marker_normal = _normalize(np.asarray(marker_normal, float))
    pts = np.asarray(skin_points, float)
    if len(pts) < min_points:
        return OrientationChoice(marker_normal, "aruco", marker_normal, None, None, None)
    if len(pts) > max_points:  # a dense reconstruction has 100k+ skin points; subsample
        pts = pts[np.random.default_rng(seed).choice(len(pts), max_points, replace=False)]
    fit = ransac_plane_normal(pts, threshold=threshold_mm, seed=seed, orient_toward=orient_toward)
    angle = angle_between_axes_deg(fit.normal, marker_normal)
    if fit.inlier_fraction < min_inlier_fraction or angle > max_deviation_deg:
        return OrientationChoice(
            marker_normal, "aruco", marker_normal, fit.normal, angle, fit.inlier_fraction
        )
    return OrientationChoice(
        _normalize(fit.normal),
        "aruco+ransac",
        marker_normal,
        fit.normal,
        angle,
        fit.inlier_fraction,
    )
