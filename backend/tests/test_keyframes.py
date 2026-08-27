"""Parity-focused unit tests for the sampling schedule (P1-2). These need no
ffmpeg — they pin the exact timestamps/counts the legacy exporter would produce,
which is the ticket's parity target. Once fixtures land (P0-3), a further test
compares extracted frame counts against fixture manifests."""

from __future__ import annotations

import math

import pytest

from app import keyframes as kf


def test_clamp_interval_bounds():
    assert kf.clamp_interval(0.001) == kf.MIN_INTERVAL_SECONDS
    assert kf.clamp_interval(99.0) == kf.MAX_INTERVAL_SECONDS
    assert kf.clamp_interval(0.35) == 0.35


def test_clamp_frame_cap_bounds():
    assert kf.clamp_frame_cap(1) == kf.MIN_FRAME_CAP
    assert kf.clamp_frame_cap(9999) == kf.MAX_FRAME_CAP
    assert kf.clamp_frame_cap(200) == 200


def test_sample_times_small_known_case():
    # duration 1.0s, interval 0.35s → limit 0.98 → t = 0.0, 0.35, 0.70
    times = kf.sample_times(1.0, interval_seconds=0.35, max_frames=100)
    assert times == [0.0, 0.35, 0.70]


def test_sample_times_starts_at_zero_and_is_evenly_spaced():
    times = kf.sample_times(10.0, interval_seconds=0.35, max_frames=350)
    assert times[0] == 0.0
    for a, b in zip(times, times[1:], strict=False):
        assert math.isclose(b - a, 0.35, abs_tol=1e-6)
    assert all(t < 10.0 - kf.DURATION_EPSILON for t in times)


def test_sample_times_respects_frame_cap():
    # min clamp forces cap >= 100; plenty of duration at min interval hits it exactly.
    times = kf.sample_times(100.0, interval_seconds=0.03, max_frames=5)
    assert len(times) == kf.MIN_FRAME_CAP  # 5 clamps up to 100


def test_sample_times_rejects_unusable_duration():
    with pytest.raises(kf.KeyframeError):
        kf.sample_times(0.04)


def test_single_pass_matches_schedule(tmp_path):
    """The one-process fps path yields the same frame count as `sample_times`."""
    import shutil
    import subprocess

    import pytest

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    from app import keyframes as kf

    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30",
            "-t",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    params = kf.KeyframeParams(interval_seconds=0.35, max_frames=350)
    fast = kf.extract_keyframes(clip, tmp_path / "fast", params)
    slow = kf.extract_keyframes(clip, tmp_path / "slow", params, single_pass=False)
    expected = kf.sample_times(kf.probe_duration(clip), 0.35, 350)
    assert fast.count == slow.count == len(expected)
    assert fast.calibration_path is not None


def test_target_frames_spreads_the_schedule():
    from app import keyframes as kf

    p = kf.KeyframeParams(target_frames=40)
    assert len(kf.sample_times(15.0, p.interval_for(15.0), 350)) in (40, 41)
    assert len(kf.sample_times(30.0, p.interval_for(30.0), 350)) in (40, 41)
    # legacy behaviour untouched without a target
    assert kf.KeyframeParams().interval_for(30.0) == 0.35
    # very short clips hit the interval floor, never zero
    assert kf.KeyframeParams(target_frames=400).interval_for(3.0) == kf.MIN_INTERVAL_SECONDS
