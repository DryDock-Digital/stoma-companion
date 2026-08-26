"""Keyframe stage: pending → extracting → keyframes_ready.

Bridges the API (P1-1) and the keyframe extractor (P1-2): pulls the uploaded
video from storage, runs extraction locally, pushes the frames back, and advances
the job so a reconstruction worker can claim it (P1-3).

It is a *queue stage* like the others — `KeyframeWorker` claims `pending` jobs
atomically (bumping `attempts`) — not a FastAPI background task, so an API
restart mid-extraction leaves a `extracting` row the stale-claim watchdog returns
to `pending`, instead of a job that spins forever. It runs as a thread inside the
API process by default (`RUN_KEYFRAME_WORKER=1`) or standalone:
`python -m app.keyframe_worker`.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from . import paths
from .cycle_time import StageTimer
from .errors import StageError, failure_fields
from .keyframes import KeyframeParams, extract_keyframes
from .models import Job, JobStatus
from .queue import _Poller
from .store import JobStore

log = logging.getLogger(__name__)


class ExtractError(StageError):
    stage = "extract"


def run_keyframe_stage(store: JobStore, job: Job | str, params: KeyframeParams) -> None:
    """Extract keyframes for a job already claimed into `extracting`. Marks the
    job `keyframes_ready` or `failed`."""
    job_id = job if isinstance(job, str) else job.id
    job = store.get_job(job_id)
    if job is None:
        log.warning("keyframe stage: job %s missing", job_id)
        return
    try:
        if job.video_path is None:
            raise ExtractError("job has no uploaded video")
        timer = StageTimer()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with timer.stage("download"):
                video_bytes = store.get_object(job.video_path)
            local_video = tmp_dir / "input.mov"
            local_video.write_bytes(video_bytes)

            out_dir = tmp_dir / "keyframes"
            with timer.stage("extract"):
                result = extract_keyframes(local_video, out_dir, params)

            with timer.stage("upload"):
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

        store.patch_result(job_id, {"timings_s": {"extract": timer.get("extract")}})
        store.update_job(
            job_id,
            status=JobStatus.KEYFRAMES_READY,
            keyframes_prefix=paths.keyframes_prefix(job_id),
            keyframe_count=result.count,
            attempts=0,  # attempts count per stage
        )
        log.info("keyframe stage: job %s -> keyframes_ready (%d frames)", job_id, result.count)
    except Exception as exc:  # noqa: BLE001 — surface any failure onto the job row
        log.exception("keyframe stage failed for job %s", job_id)
        try:
            store.update_job(job_id, status=JobStatus.FAILED, **failure_fields(exc, "extract"))
        except Exception:  # noqa: BLE001
            log.exception("could not record keyframe failure for job %s", job_id)


class KeyframeWorker(_Poller):
    """Claims `pending` jobs and runs the keyframe stage. `processor` is injectable
    so tests can swap the ffmpeg-backed stage for a stub."""

    def __init__(
        self,
        store: JobStore,
        params: KeyframeParams,
        worker_id: str = "keyframes-1",
        processor=run_keyframe_stage,
        **poller_kwargs,
    ) -> None:
        super().__init__(store, worker_id, **poller_kwargs)
        self.params = params
        self.processor = processor

    def claim(self) -> Job | None:
        return self.store.claim_next_job(
            self.worker_id, None, JobStatus.PENDING, JobStatus.EXTRACTING
        )

    def run_once(self) -> bool:
        job = self.claim()
        if job is None:
            return False
        with self.heartbeat(job.id):
            self.processor(self.store, job, self.params)
        return True
