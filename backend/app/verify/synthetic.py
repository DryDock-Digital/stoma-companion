"""Synthetic ArUco scenes for orientation scoring (P2-2).

Places a marker on a plane with a *known* normal, orbits pinhole cameras around it
at known poses, and renders each view by warping the canonical ArUco image into the
projected marker quad (white quiet zone falls out for free). This is the ground
truth the recovered orientation is scored against — no physical capture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from ..measure.aruco import ARUCO_DICT, detect_markers
from ..measure.orientation import PinholeCamera

MARKER_PX = 240  # canonical marker render size


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """In-plane right/down (u, v) with u × v = normal."""
    n = _normalize(np.asarray(normal, dtype=float))
    ref = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = _normalize(np.cross(ref, n))
    v = _normalize(np.cross(n, u))
    return u, v


def skin_point_cloud(
    normal,
    center=(0.0, 0.0, 0.0),
    *,
    radius: float = 45.0,
    n_points: int = 1500,
    noise: float = 0.2,
    stoma_radius: float = 10.0,
    stoma_bump: float = 6.0,
    outlier_frac: float = 0.03,
    outlier_scale: float = 10.0,
    seed: int = 0,
) -> np.ndarray:
    """A peristomal-skin patch: a disk in the plane (`normal`) with Gaussian
    measurement noise, a central raised stoma (non-planar → outliers to the skin
    plane), and a few gross reconstruction outliers. Same known normal as the
    marker scene, so RANSAC/PCA are scored on the same board as the ArUco plane."""
    rng = np.random.default_rng(seed)
    n = _normalize(np.asarray(normal, dtype=float))
    center = np.asarray(center, dtype=float)
    u, v = plane_basis(n)

    rho = radius * np.sqrt(rng.random(n_points))
    theta = 2 * math.pi * rng.random(n_points)
    inplane = rho[:, None] * (np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v)

    z = noise * rng.standard_normal(n_points)
    z += (rho < stoma_radius) * stoma_bump  # central stoma rises off the skin plane
    outliers = rng.random(n_points) < outlier_frac
    z += outliers * rng.standard_normal(n_points) * outlier_scale

    return center + inplane + z[:, None] * n


def marker_corners_3d(center, u, v, side: float) -> np.ndarray:
    """(4,3) corners TL, TR, BR, BL for canonical-image order."""
    center = np.asarray(center, dtype=float)
    h = side / 2
    return np.array(
        [
            center - u * h - v * h,
            center + u * h - v * h,
            center + u * h + v * h,
            center - u * h + v * h,
        ]
    )


def orbit_cameras(
    center,
    u,
    v,
    normal,
    *,
    radius: float,
    count: int,
    elevation_deg: float,
    image_size: tuple[int, int],
    fov_deg: float,
) -> list[PinholeCamera]:
    """`count` cameras on a cone around the normal (on the +normal side), each
    looking at `center`."""
    center = np.asarray(center, dtype=float)
    elev = math.radians(elevation_deg)
    cams: list[PinholeCamera] = []
    for i in range(count):
        phi = 2 * math.pi * i / count
        direction = math.sin(elev) * normal + math.cos(elev) * (
            math.cos(phi) * u + math.sin(phi) * v
        )
        eye = center + radius * _normalize(direction)
        cams.append(PinholeCamera.look_at(eye, center, image_size=image_size, fov_deg=fov_deg))
    return cams


def render_marker_view(
    camera: PinholeCamera,
    corners3d: np.ndarray,
    marker_img: np.ndarray,
    image_size: tuple[int, int],
) -> np.ndarray:
    """Warp the marker into `camera`'s view; outside the quad is white (quiet zone)."""
    h, w = marker_img.shape[:2]
    src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    dst = camera.project(corners3d).astype(np.float32)
    h_mat = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        marker_img,
        h_mat,
        image_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


@dataclass
class SyntheticScene:
    name: str
    true_normal: np.ndarray
    center: np.ndarray
    side: float
    corners3d: np.ndarray
    cameras: list[PinholeCamera]
    marker_id: int
    image_size: tuple[int, int]
    marker_img: np.ndarray
    skin_points: np.ndarray | None = None  # for marker-independent methods (P2-3)

    def render_views(self) -> list[np.ndarray]:
        return [
            render_marker_view(c, self.corners3d, self.marker_img, self.image_size)
            for c in self.cameras
        ]


def build_scene(
    name: str,
    normal,
    *,
    center=(0.0, 0.0, 0.0),
    side: float = 40.0,
    marker_id: int = 7,
    n_views: int = 12,
    radius: float = 100.0,
    elevation_deg: float = 60.0,
    image_size: tuple[int, int] = (800, 800),
    fov_deg: float = 55.0,
    with_skin: bool = False,
    skin_kwargs: dict | None = None,
) -> SyntheticScene:
    """A detectable synthetic scene with a known plane normal. Auto-flips the
    marker's in-plane handedness if the first render doesn't decode (so a marker
    facing the cameras is never accidentally mirrored). With `with_skin`, also
    attaches a skin point cloud sharing the same normal (for RANSAC/PCA, P2-3)."""
    normal = _normalize(np.asarray(normal, dtype=float))
    center = np.asarray(center, dtype=float)
    u, v = plane_basis(normal)
    skin_points = skin_point_cloud(normal, center, **(skin_kwargs or {})) if with_skin else None
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, ARUCO_DICT))
    marker_img = cv2.aruco.generateImageMarker(dictionary, marker_id, MARKER_PX)
    cameras = orbit_cameras(
        center,
        u,
        v,
        normal,
        radius=radius,
        count=n_views,
        elevation_deg=elevation_deg,
        image_size=image_size,
        fov_deg=fov_deg,
    )

    for flip in (False, True):
        vv = -v if flip else v
        corners3d = marker_corners_3d(center, u, vv, side)
        probe = render_marker_view(cameras[0], corners3d, marker_img, image_size)
        if any(d.marker_id == marker_id for d in detect_markers(probe)):
            return SyntheticScene(
                name=name,
                true_normal=normal,
                center=center,
                side=side,
                corners3d=corners3d,
                cameras=cameras,
                marker_id=marker_id,
                image_size=image_size,
                marker_img=marker_img,
                skin_points=skin_points,
            )
    raise RuntimeError(f"scene '{name}': marker not detectable in the probe view")
