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
    #: when set, the interval is chosen so ~this many frames span the *whole* clip
    #: (a fixed interval + cap would truncate a long orbit instead of thinning it).
    #: The caliper sweep put the accuracy/speed sweet spot at ~40 frames (D19).
    target_frames: int | None = None

    def interval_for(self, duration_seconds: float) -> float:
        if self.target_frames and self.target_frames > 0 and duration_seconds > 0:
            return clamp_interval(duration_seconds / self.target_frames)
        return self.interval_seconds


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


def _scale_filter(params: KeyframeParams) -> str:
    # Downscale only if larger than max_dimension, preserving aspect (mirrors the
    # generator.maximumSize behaviour).
    return (
        f"scale='min({params.max_dimension},iw)':'min({params.max_dimension},ih)'"
        ":force_original_aspect_ratio=decrease"
    )


def _jpeg_q(params: KeyframeParams) -> str:
    # ffmpeg -q:v 2 (best) … 31; map the legacy 0–1 quality onto that range
    q = 2 + round((1.0 - max(0.0, min(1.0, params.jpeg_quality))) * 29)
    return str(max(2, min(31, q)))


def _extract_frame(
    video_path: Path, timestamp: float, out_path: Path, params: KeyframeParams
) -> bool:
    """Seek to `timestamp` and write a single downscaled JPEG (used for the t=0
    calibration still and as the per-frame fallback)."""
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
            _scale_filter(params),
            "-q:v",
            _jpeg_q(params),
            "-y",
            str(out_path),
        ],
        capture_output=True,
    )
    return result.returncode == 0 and out_path.exists()


def _extract_all_single_pass(
    video_path: Path, output_dir: Path, times: list[float], params: KeyframeParams
) -> list[Path]:
    """One ffmpeg process for the whole schedule: `fps=1/interval` emits the frame
    nearest t = 0, interval, 2·interval … — exactly `sample_times()` — capped at
    len(times). ~10× faster than one seek+decode process per frame (36 s → ~3 s on
    the first real 30 s clip)."""
    interval = clamp_interval(params.interval_for(probe_duration(video_path)))
    pattern = output_dir / "frame_%05d.jpg"
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{interval:.6f}:round=near,{_scale_filter(params)}",
            "-frames:v",
            str(len(times)),
            "-q:v",
            _jpeg_q(params),
            "-start_number",
            "0",
            "-y",
            str(pattern),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise KeyframeError(f"ffmpeg keyframe pass failed: {result.stderr[-400:]}")
    return sorted(output_dir.glob("frame_*.jpg"))


def extract_keyframes(
    video_path: Path,
    output_dir: Path,
    params: KeyframeParams | None = None,
    *,
    single_pass: bool = True,
) -> KeyframeResult:
    """Extract keyframes from a local video into `output_dir`.

    Frames are named frame_00000.jpg… with dense numbering, exactly like the legacy
    exporter. A t=0 `calibration_top.jpg` still is written first (best-effort). The
    default is one ffmpeg pass over the clip; `single_pass=False` keeps the legacy
    seek-per-frame path (fallback if a container confuses the fps filter).
    """
    _require_binaries()
    params = params or KeyframeParams()
    output_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(video_path)
    interval = params.interval_for(duration)
    times = sample_times(duration, interval, params.max_frames)
    if not times:
        raise KeyframeError("Interval is too long for this video length.")

    # Exact first-frame still for calibration (non-fatal on failure).
    calibration_path: Path | None = output_dir / "calibration_top.jpg"
    if not _extract_frame(video_path, 0.0, calibration_path, params):
        calibration_path = None

    frame_paths: list[Path] = []
    if single_pass:
        try:
            frame_paths = _extract_all_single_pass(video_path, output_dir, times, params)
        except KeyframeError:
            frame_paths = []
    if len(frame_paths) < MIN_USABLE_FRAMES:
        for f in frame_paths:
            f.unlink(missing_ok=True)
        frame_paths = []
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
        sample_times=times[: len(frame_paths)],
    )
