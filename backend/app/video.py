"""Fit an uploaded video under the storage object cap.

Supabase rejects objects over its per-project limit (50 MB on the current plan); a
20-second 4K phone clip is 60–120 MB. Rather than bounce the patient, re-encode
with ffmpeg at the same resolution (H.264, CRF 20 → visually lossless for
photogrammetry; a second pass at CRF 26 if still too big) and store that. The
keyframe extractor reads whatever we store, so nothing downstream changes.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import StageError

log = logging.getLogger(__name__)

TOO_LONG_MESSAGE = "That video is too long. Please record a shorter one — about 20 seconds."


class VideoTooLarge(StageError):
    stage = "upload"

    def __init__(self, detail: str):
        super().__init__(detail, user_message=TOO_LONG_MESSAGE)


@dataclass
class FittedVideo:
    data: bytes
    content_type: str
    original_bytes: int
    transcoded: bool
    crf: int | None = None


def fit_video(
    data: bytes,
    content_type: str,
    *,
    max_bytes: int,
    crf_steps: tuple[int, ...] = (20, 26),
    timeout_s: float = 600.0,
) -> FittedVideo:
    """Return `data` unchanged if it fits, else an H.264 re-encode that does."""
    if len(data) <= max_bytes:
        return FittedVideo(data, content_type, len(data), False)
    if shutil.which("ffmpeg") is None:
        raise VideoTooLarge(f"{len(data)} bytes > cap {max_bytes} and ffmpeg is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.bin"
        src.write_bytes(data)
        for crf in crf_steps:
            dst = Path(tmp) / f"out_{crf}.mp4"
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(src),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-an",
                    str(dst),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            if proc.returncode != 0 or not dst.exists():
                raise StageError(f"ffmpeg re-encode failed: {proc.stderr[-500:]}", stage="extract")
            out = dst.read_bytes()
            log.info("video re-encoded crf=%d: %d → %d bytes", crf, len(data), len(out))
            if len(out) <= max_bytes:
                return FittedVideo(out, "video/mp4", len(data), True, crf)
    raise VideoTooLarge(f"{len(data)} bytes; still over {max_bytes} after re-encode")
