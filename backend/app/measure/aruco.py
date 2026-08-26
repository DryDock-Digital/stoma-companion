"""ArUco detection + scale derivation (P1-6).

Ports two legacy pieces:
  - CompanionMac/ArUcoDetectorBridge.mm — DICT_4X4_50 detection + a pixel→mm
    homography from a known-size marker. Here it uses OpenCV's `cv2.aruco` directly
    (the bridge's hand-rolled decoder is explicitly "verified against OpenCV
    generateImageMarker(DICT_4X4_50, …)", so cv2.aruco is the canonical equivalent).
  - CompanionMac/MeshArUcoOrbitDetector.swift — the scale *derivation*: measure the
    marker's edge length in mesh/scene units from its 3-D corners, then
    scale = marker_side_mm / mean_side_scene (millimetres per scene unit). This is
    the factor the reconstructed mesh is multiplied by to reach real-world mm.

cv2 is imported lazily so the pure-numpy scale math (refine_planar_square,
scale_from_marker_corners) works — and is tested — even where OpenCV isn't
installed. The scale factor is the ticket's parity target.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ARUCO_DICT = "DICT_4X4_50"  # default only; every detector call accepts a dictionary name
MAX_SIDE_CV = 0.12  # MeshArUcoOrbitDetector.maxSideCV — reject inconsistent squares


# --- image-space detection (OpenCV) ----------------------------------------


@dataclass
class MarkerDetection:
    marker_id: int
    corners_px: np.ndarray  # (4,2) TL, TR, BR, BL — cv2.aruco order


def _aruco_dictionary(name: str = ARUCO_DICT):
    import cv2  # lazy

    if not hasattr(cv2.aruco, name):
        raise ValueError(f"unknown ArUco dictionary {name!r}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def quad_area_px(corners: np.ndarray) -> float:
    """Area of the detected marker quad in pixels² (shoelace) — a view-quality weight."""
    c = np.asarray(corners, dtype=float)
    x, y = c[:, 0], c[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def detect_markers(image: np.ndarray, dictionary_name: str = ARUCO_DICT) -> list[MarkerDetection]:
    """Detect markers from `dictionary_name` (default DICT_4X4_50). `image` is a
    grayscale or BGR uint8 array."""
    import cv2

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dictionary = _aruco_dictionary(dictionary_name)
    # Support both the new (>=4.7) and legacy cv2.aruco APIs.
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        corners, ids, _ = detector.detectMarkers(gray)
    else:  # pragma: no cover - exercised only on old OpenCV
        corners, ids, _ = cv2.aruco.detectMarkers(gray, dictionary)

    if ids is None:
        return []
    out: list[MarkerDetection] = []
    for marker_corners, marker_id in zip(corners, ids.flatten(), strict=False):
        out.append(MarkerDetection(int(marker_id), marker_corners.reshape(4, 2).astype(float)))
    return out


@dataclass
class HomographyResult:
    marker_id: int
    matrix: np.ndarray  # 3x3, pixel → mm
    marker_side_mm: float
    mean_corner_residual_mm: float

    def apply(self, pixel: tuple[float, float]) -> tuple[float, float]:
        return apply_homography(self.matrix, pixel)


def pixel_to_mm_homography(
    image: np.ndarray,
    marker_side_mm: float,
    expected_id: int = -1,
    dictionary_name: str = ARUCO_DICT,
) -> HomographyResult | None:
    """Planar pixel→mm homography from a known-size marker (ArUcoDetectorBridge
    homographyFromImage). Picks `expected_id` if present, else the first marker."""
    import cv2

    detections = detect_markers(image, dictionary_name)
    if not detections:
        return None
    chosen = next((d for d in detections if d.marker_id == expected_id), detections[0])

    # Marker corners map to a square of side marker_side_mm, TL→TR→BR→BL.
    dst = np.array(
        [[0, 0], [marker_side_mm, 0], [marker_side_mm, marker_side_mm], [0, marker_side_mm]],
        dtype=np.float64,
    )
    h_matrix = cv2.getPerspectiveTransform(
        chosen.corners_px.astype(np.float32), dst.astype(np.float32)
    ).astype(np.float64)

    residuals = [
        float(np.hypot(*(np.array(apply_homography(h_matrix, tuple(px))) - dst[i])))
        for i, px in enumerate(chosen.corners_px)
    ]
    return HomographyResult(
        marker_id=chosen.marker_id,
        matrix=h_matrix,
        marker_side_mm=marker_side_mm,
        mean_corner_residual_mm=float(np.mean(residuals)),
    )


def apply_homography(matrix: np.ndarray, pixel: tuple[float, float]) -> tuple[float, float]:
    v = matrix @ np.array([pixel[0], pixel[1], 1.0])
    w = v[2] if abs(v[2]) > 1e-12 else 1e-12
    return (float(v[0] / w), float(v[1] / w))


# --- 3-D scale derivation (pure numpy) -------------------------------------


def refine_planar_square(corners: np.ndarray) -> np.ndarray:
    """Project 4 corners onto their best-fit plane (MeshArUcoOrbitDetector.refinePlanarSquare)."""
    corners = np.asarray(corners, dtype=float)
    if len(corners) != 4:
        return corners
    center = corners.mean(axis=0)
    n = np.cross(corners[1] - corners[0], corners[3] - corners[0])
    norm = np.linalg.norm(n)
    n = n / norm if norm > 1e-12 and np.isfinite(norm) else np.array([0.0, 1.0, 0.0])
    return np.array([p - n * float((p - center) @ n) for p in corners])


class InconsistentSquareError(RuntimeError):
    pass


@dataclass
class ScaleResult:
    mean_side_scene: float
    side_cv: float
    scale_mm_per_scene_unit: float
    world_corners: np.ndarray

    @property
    def passes(self) -> bool:
        return self.side_cv <= MAX_SIDE_CV


def scale_from_marker_corners(world_corners: np.ndarray, marker_side_mm: float) -> ScaleResult:
    """Mesh/scene-unit → mm scale from the marker's 3-D corners.

    Refines the corners onto a plane, measures the four side lengths, and returns
    scale = marker_side_mm / mean_side. Raises if the sides are too inconsistent
    (coefficient of variation > 12%), mirroring the legacy gate.
    """
    if not (0.5 <= marker_side_mm <= 500):
        raise InconsistentSquareError("Marker side must be between 0.5 and 500 mm.")
    refined = refine_planar_square(world_corners)
    if len(refined) != 4:
        raise InconsistentSquareError("Need exactly 4 marker corners.")

    sides = np.array([np.linalg.norm(refined[i] - refined[(i + 1) % 4]) for i in range(4)])
    mean_side = float(sides.mean())
    if mean_side <= 1e-8:
        raise InconsistentSquareError("Degenerate marker (zero side length).")
    variance = float(np.mean((sides - mean_side) ** 2))
    cv = variance**0.5 / mean_side
    if cv > MAX_SIDE_CV:
        raise InconsistentSquareError(
            f"Unprojected marker sides inconsistent (CV {cv * 100:.1f}%)."
        )
    return ScaleResult(
        mean_side_scene=mean_side,
        side_cv=cv,
        scale_mm_per_scene_unit=marker_side_mm / mean_side,
        world_corners=refined,
    )
