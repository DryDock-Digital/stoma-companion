"""Keyframe extraction — ffmpeg port of legacy SharedPhotogrammetry/
VideoFrameExporter.swift (P1-2).

The parity-critical part is the *sampling schedule*: which timestamps get a
frame, and how many. That lives in `sample_times()`, a pure function that mirrors
the Swift loop exactly so it can be unit-tested against fixture frame
counts/timings without ffmpeg or a GPU. Pixel-for-pixel parity across AVFoundation
vs ffmpeg is neither achievable nor required — the ticket's parity target is
"frame count/timing vs fixtures".

Legacy constants (VideoFrameExporter.swift):
    minIntervalSeconds  0.03   maxIntervalSeconds 1.0    defaultIntervalSeconds 0.35
    minFrameCap        100     maxFrameCap        500    defaultFrameCap        350
Sampling loop: t = 0; while t < duration - 0.02 and count < cap: emit t; t += interval.
Also emits an exact t=0 still as `calibration_top.jpg`, and requires >= 8 frames.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# --- legacy constants (keep in lockstep with VideoFrameExporter.swift) ------
MIN_INTERVAL_SECONDS = 0.03
MAX_INTERVAL_SECONDS = 1.0
DEFAULT_INTERVAL_SECONDS = 0.35
MIN_FRAME_CAP = 100
MAX_FRAME_CAP = 500
DEFAULT_FRAME_CAP = 350
MIN_USABLE_FRAMES = 8
DURATION_EPSILON = 0.02  # legacy stops the loop at duration - 0.02
MAX_DIMENSION = 2560  # legacy generator.maximumSize
DEFAULT_JPEG_QUALITY = 0.9


class KeyframeError(RuntimeError):
    pass


def clamp_interval(seconds: float) -> float:
    """Mirror VideoFrameExporter.clampInterval."""
    return min(MAX_INTERVAL_SECONDS, max(MIN_INTERVAL_SECONDS, seconds))


def clamp_frame_cap(count: int) -> int:
    """Mirror VideoFrameExporter.clampFrameCap."""
    return min(MAX_FRAME_CAP, max(MIN_FRAME_CAP, count))


def sample_times(
    duration_seconds: float,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_frames: int = DEFAULT_FRAME_CAP,
) -> list[float]:
    """The exact set of timestamps the legacy exporter samples.

    Inputs are clamped first (as the Swift does), then the loop runs:
        t = 0; while t < duration - 0.02 and len < cap: append t; t += interval.
    Returns [] when the interval is too long for the clip.
    """
    if not (duration_seconds > 0.05):
        raise KeyframeError("Video has no usable duration.")

    interval = clamp_interval(interval_seconds)
    cap = clamp_frame_cap(max_frames)

    times: list[float] = []
    t = 0.0
    limit = duration_seconds - DURATION_EPSILON
    while t < limit and len(times) < cap:
        times.append(round(t, 6))
        t += interval
    return times


@dataclass
class KeyframeParams:
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    max_frames: int = DEFAULT_FRAME_CAP
    jpeg_quality: float = DEFAULT_JPEG_QUALITY
    max_dimension: int = MAX_DIMENSION


@dataclass
class KeyframeResult:
    count: int
    frame_paths: list[Path]
    calibration_path: Path | None
    sample_times: list[float] = field(default_factory=list)

    def manifest(self) -> dict:
        """Timing manifest — the artifact parity tests compare against fixtures."""
        return {
            "count": self.count,
            "sample_times": self.sample_times,
            "frames": [p.name for p in self.frame_paths],
            "calibration": self.calibration_path.name if self.calibration_path else None,
        }


def _require_binaries() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise KeyframeError(f"{binary} not found on PATH")


def probe_duration(video_path: Path) -> float:
    """Clip duration in seconds via ffprobe."""
    _require_binaries()
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise KeyframeError(f"Could not read duration from {video_path}") from exc


def _extract_frame(
    video_path: Path, timestamp: float, out_path: Path, params: KeyframeParams
) -> bool:
    """Seek to `timestamp` and write a single downscaled JPEG. Returns success;
    the legacy loop tolerates individual failed frames (`continue`)."""
    # Downscale only if larger than max_dimension, preserving aspect (mirrors the
    # generator.maximumSize behaviour). -q:v 2 ≈ high-quality JPEG.
    scale = (
        f"scale='min({params.max_dimension},iw)':'min({params.max_dimension},ih)'"
        ":force_original_aspect_ratio=decrease"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            "2",
            "-y",
            str(out_path),
        ],
        capture_output=True,
    )
    return result.returncode == 0 and out_path.exists()


def extract_keyframes(
    video_path: Path,
    output_dir: Path,
    params: KeyframeParams | None = None,
) -> KeyframeResult:
    """Extract keyframes from a local video into `output_dir`.

    Frames are named frame_00000.jpg… and re-indexed by successful writes (so the
    numbering is dense even if a seek fails), exactly like the legacy exporter.
    A t=0 `calibration_top.jpg` still is written first (best-effort).
    """
    _require_binaries()
    params = params or KeyframeParams()
    output_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(video_path)
    times = sample_times(duration, params.interval_seconds, params.max_frames)
    if not times:
        raise KeyframeError("Interval is too long for this video length.")

    # Exact first-frame still for calibration (non-fatal on failure).
    calibration_path: Path | None = output_dir / "calibration_top.jpg"
    if not _extract_frame(video_path, 0.0, calibration_path, params):
        calibration_path = None

    frame_paths: list[Path] = []
    for timestamp in times:
        out_path = output_dir / f"frame_{len(frame_paths):05d}.jpg"
        if _extract_frame(video_path, timestamp, out_path, params):
            frame_paths.append(out_path)

    if len(frame_paths) < MIN_USABLE_FRAMES:
        raise KeyframeError(
            f"Too few frames extracted ({len(frame_paths)}; need >= {MIN_USABLE_FRAMES})."
        )

    return KeyframeResult(
        count=len(frame_paths),
        frame_paths=frame_paths,
        calibration_path=calibration_path,
        sample_times=times,
    )
