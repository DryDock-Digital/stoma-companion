"""Post-upload keyframe stage: pending → extracting → keyframes_ready.

Bridges the API (P1-1) and the keyframe extractor (P1-2): pulls the uploaded
video from storage, runs extraction locally, pushes the frames back, and advances
the job so a reconstruction worker can claim it (P1-3). Runs as a FastAPI
background task; on any failure the job is marked `failed` with the error text.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from . import paths
from .cycle_time import StageTimer, merge_timing
from .keyframes import KeyframeParams, extract_keyframes
from .models import JobStatus
from .store import JobStore

log = logging.getLogger(__name__)


def run_keyframe_stage(store: JobStore, job_id: str, params: KeyframeParams) -> None:
    job = store.get_job(job_id)
    if job is None or job.video_path is None:
        log.warning("keyframe stage: job %s missing or has no video", job_id)
        return

    try:
        store.update_job(job_id, status=JobStatus.EXTRACTING)
        timer = StageTimer()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            video_bytes = store.get_object(job.video_path)
            local_video = tmp_dir / "input.mov"
            local_video.write_bytes(video_bytes)

            out_dir = tmp_dir / "keyframes"
            with timer.stage("extract"):
                result = extract_keyframes(local_video, out_dir, params)

            if result.calibration_path is not None:
                store.put_object(
                    paths.calibration_key(job_id),
                    result.calibration_path.read_bytes(),
                    "image/jpeg",
                )
            for index, frame in enumerate(result.frame_paths):
                store.put_object(
                    paths.keyframe_key(job_id, index),
                    frame.read_bytes(),
                    "image/jpeg",
                )

        store.update_job(
            job_id,
            status=JobStatus.KEYFRAMES_READY,
            keyframes_prefix=paths.keyframes_prefix(job_id),
            keyframe_count=result.count,
            result=merge_timing(job.result, "extract", timer.get("extract")),
        )
        log.info("keyframe stage: job %s -> keyframes_ready (%d frames)", job_id, result.count)
    except Exception as exc:  # noqa: BLE001 — surface any failure onto the job row
        log.exception("keyframe stage failed for job %s", job_id)
        store.update_job(job_id, status=JobStatus.FAILED, error=str(exc))
