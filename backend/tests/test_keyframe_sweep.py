"""P2-5 keyframe-minimization rig. Validated with a fake engine that *degrades* at
low frame counts — the rig logic (sweep, deviation, timing, min-passing) is what's
under test here; the real deviation curve comes from COLMAP on real footage."""

from __future__ import annotations

from pathlib import Path

import pytest

trimesh = pytest.importorskip("trimesh")

from app.verify.keyframe_sweep import run_keyframe_sweep, subsample_frames  # noqa: E402


def _make_clock():
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    return clock


class DegradingEngine:
    """A stand-in: reconstruction quality rises with frame count and plateaus at 100
    frames (radius approaches the true 16.5 mm → 33 mm diameter)."""

    name = "fake-degrading"

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> Path:
        n = len(list(keyframe_dir.glob("frame_*.jpg")))
        quality = min(1.0, n / 100)
        radius = 16.5 * (0.5 + 0.5 * quality)  # 20f→9.9, 50f→12.4, ≥100f→16.5
        mesh = trimesh.creation.cylinder(radius=radius, height=10, sections=64)
        out = work_dir / "mesh.obj"
        mesh.export(out)
        return out


def test_subsample_frames():
    paths = [Path(f"frame_{i:05d}.jpg") for i in range(10)]
    picked = subsample_frames(paths, 4)
    assert len(picked) == 4
    assert picked[0] == paths[0] and picked[-1] == paths[-1]
    assert subsample_frames(paths, 20) == paths  # asking for more than available


def test_sweep_finds_min_passing_frames(tmp_path):
    frames = []
    for i in range(120):
        p = tmp_path / f"frame_{i:05d}.jpg"
        p.write_bytes(b"x")  # content ignored by the fake engine
        frames.append(p)

    result = run_keyframe_sweep(
        frames,
        truth_mm=33.0,
        reconstructor=DegradingEngine(),
        frame_counts=(20, 50, 100, 350),
        tolerance_mm=1.0,
        clock=_make_clock(),
    )

    by_count = {r.frame_count: r for r in result.rows}
    assert set(by_count) == {20, 50, 100, 120}  # 350 clamps to the 120 available

    # deviation shrinks as frames grow; only ≥100 frames pass ±1 mm
    assert abs(by_count[20].deviation_mm) > abs(by_count[50].deviation_mm)
    assert not by_count[20].passed and not by_count[50].passed
    assert by_count[100].passed and by_count[120].passed
    assert result.min_passing_frames == 100

    # timing recorded for every run
    assert all(r.reconstruct_s > 0 and r.measure_s > 0 for r in result.rows)

    table = result.format_table()
    assert "deviation vs frames" in table and "runtime vs frames" in table
    assert "min frames within tolerance: 100" in table
    assert result.to_csv().splitlines()[0].startswith("frame_count,reconstruct_s")
