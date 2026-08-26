"""P1-6 ArUco port. The scale math is pure numpy (always run); the cv2 detection
path runs when OpenCV is installed."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.measure import aruco


def _rot(ax, ay, az):
    rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    return rz @ ry @ rx


def test_scale_from_marker_corners():
    s = 0.02  # marker side in scene units
    square = np.array([[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0]], dtype=float)
    tilted = square @ _rot(0.3, -0.2, 0.5).T  # arbitrary planar tilt, sides preserved

    result = aruco.scale_from_marker_corners(tilted, marker_side_mm=20.0)
    assert math.isclose(result.mean_side_scene, s, rel_tol=1e-6)
    assert result.side_cv < 1e-6
    assert math.isclose(result.scale_mm_per_scene_unit, 1000.0, rel_tol=1e-6)
    assert result.passes


def test_refine_planar_square_projects_to_plane():
    noisy = np.array([[0, 0, 0.001], [1, 0, -0.001], [1, 1, 0.002], [0, 1, -0.002]], dtype=float)
    refined = aruco.refine_planar_square(noisy)
    n = np.cross(refined[1] - refined[0], refined[3] - refined[0])
    n /= np.linalg.norm(n)
    # all four corners coplanar → zero out-of-plane spread
    d = refined @ n
    assert d.max() - d.min() < 1e-6


def test_inconsistent_square_rejected():
    rect = np.array([[0, 0, 0], [0.02, 0, 0], [0.02, 0.05, 0], [0, 0.05, 0]], dtype=float)
    with pytest.raises(aruco.InconsistentSquareError):
        aruco.scale_from_marker_corners(rect, marker_side_mm=20.0)


def test_marker_side_out_of_range_rejected():
    square = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    with pytest.raises(aruco.InconsistentSquareError):
        aruco.scale_from_marker_corners(square, marker_side_mm=0.1)


# --- cv2 detection path ----------------------------------------------------

cv2 = pytest.importorskip("cv2")


def _marker_canvas(marker_id: int = 7, px: int = 200, border: int = 60):
    # a card in the *default* dictionary (LEGACY_4X4_50 — what the Mac app prints)
    dictionary = aruco.aruco_dictionary(aruco.ARUCO_DICT)
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, px)
    canvas = np.full((px + 2 * border, px + 2 * border), 255, dtype=np.uint8)
    canvas[border : border + px, border : border + px] = marker
    return canvas


def test_detect_markers_finds_id():
    canvas = _marker_canvas(marker_id=7)
    detections = aruco.detect_markers(canvas)
    assert any(d.marker_id == 7 for d in detections)
    d = next(d for d in detections if d.marker_id == 7)
    assert d.corners_px.shape == (4, 2)


def test_pixel_to_mm_homography_maps_marker_to_square():
    canvas = _marker_canvas(marker_id=7)
    h = aruco.pixel_to_mm_homography(canvas, marker_side_mm=50.0, expected_id=7)
    assert h is not None
    assert h.marker_id == 7
    assert h.mean_corner_residual_mm < 1e-3  # 4-point homography is exact

    # the detected marker's top edge maps to a 50 mm span in mm-space
    marker = next(d for d in aruco.detect_markers(canvas) if d.marker_id == 7)
    tl = np.array(h.apply(tuple(marker.corners_px[0])))
    tr = np.array(h.apply(tuple(marker.corners_px[1])))
    assert math.isclose(float(np.linalg.norm(tr - tl)), 50.0, abs_tol=0.1)


def test_legacy_inverted_dictionary_decodes_mac_app_cards():
    """The legacy generator prints OpenCV's bit table as dark=1 → inner bits inverted.
    Build such a card the way ArUcoMarkerGenerator.swift does and check the LEGACY
    dictionary decodes it while the standard one does not."""
    import cv2

    from app.measure.aruco import aruco_dictionary, detect_markers

    std = aruco_dictionary("DICT_4X4_50")
    marker_id = 7
    bits = cv2.aruco.Dictionary.getBitsFromByteList(std.bytesList[marker_id : marker_id + 1], 4)
    cell = 40
    img = np.full((8 * cell, 8 * cell), 255, np.uint8)  # 1-cell quiet zone
    for r in range(6):
        for c in range(6):
            on_border = r in (0, 5) or c in (0, 5)
            black = on_border or bits[r - 1, c - 1] == 1  # legacy: bit 1 → black
            if black:
                img[(r + 1) * cell : (r + 2) * cell, (c + 1) * cell : (c + 2) * cell] = 0
    legacy = detect_markers(img, "LEGACY_4X4_50")
    assert [d.marker_id for d in legacy] == [marker_id]
    assert detect_markers(img, "DICT_4X4_50") == []
    # a standard OpenCV marker is NOT decoded by the legacy dictionary
    std_img = cv2.aruco.generateImageMarker(std, marker_id, 6 * cell)
    std_img = cv2.copyMakeBorder(std_img, cell, cell, cell, cell, cv2.BORDER_CONSTANT, value=255)
    assert detect_markers(std_img, "LEGACY_4X4_50") == []
    assert [d.marker_id for d in detect_markers(std_img, "DICT_4X4_50")] == [marker_id]


def test_unknown_dictionary_name_rejected():
    import pytest

    from app.measure.aruco import aruco_dictionary

    with pytest.raises(ValueError):
        aruco_dictionary("DICT_9X9_1")
