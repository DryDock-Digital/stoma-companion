"""P1-7 slice port — validated against a unit cube whose cross-section is known
analytically. Fixture-based parity (vs legacy meshes) joins once P0-3 lands."""

from __future__ import annotations

import math

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from app.measure import slicing  # noqa: E402


def _unit_cube():
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])  # centered at origin
    return np.asarray(box.vertices, dtype=float), np.asarray(box.faces, dtype=int)


def test_orthonormal_basis_is_orthonormal():
    for n in ([0, 1, 0], [1, 0, 0], [0.3, 0.5, -0.8]):
        u, v = slicing.orthonormal_basis(np.array(n, dtype=float))
        nn = slicing._normalize(np.array(n, dtype=float))
        assert abs(np.linalg.norm(u) - 1) < 1e-6
        assert abs(np.linalg.norm(v) - 1) < 1e-6
        assert abs(u @ v) < 1e-6
        assert abs(u @ nn) < 1e-6 and abs(v @ nn) < 1e-6


def test_manual_tilt_zero_returns_base_axis():
    n = slicing.plane_normal_from_manual_tilt("positiveY", 0, 0, 0)
    assert np.allclose(n, [0, 1, 0], atol=1e-6)


def test_cube_midslice_is_unit_square():
    verts, faces = _unit_cube()
    result = slicing.extract_perimeter(verts, faces, normal=[0, 1, 0], slice_offset_fraction=0.5)

    assert len(result.samples) == slicing.SAMPLE_COUNT
    assert result.loop_vertex_count >= 4
    # cross-section is a 1×1 square → longest chord is its diagonal √2.
    chord = slicing.max_planar_chord_length(result.samples)
    assert math.isclose(chord, math.sqrt(2), abs_tol=0.05)
    # plane passes through the cube centre (offset 0.5 of a [-0.5,0.5] span).
    assert math.isclose(result.plane_constant, 0.0, abs_tol=1e-6)
    assert np.allclose(result.centroid_world, [0, 0, 0], atol=0.05)


def test_offset_moves_the_plane():
    verts, faces = _unit_cube()
    low = slicing.extract_perimeter(verts, faces, [0, 1, 0], 0.25)
    high = slicing.extract_perimeter(verts, faces, [0, 1, 0], 0.75)
    assert low.plane_constant < high.plane_constant


def test_arc_length_resample_is_evenly_spaced():
    # unit circle sampled coarsely → resample to 100 near-equal steps.
    theta = np.linspace(0, 2 * math.pi, 37)[:-1]
    ring = np.column_stack([np.cos(theta), np.sin(theta)])
    samples = slicing.arc_length_resample(ring, 100)
    assert len(samples) == 100
    pts = np.array([[s.x, s.y] for s in samples])
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    assert steps.std() < 0.01  # near-uniform spacing
