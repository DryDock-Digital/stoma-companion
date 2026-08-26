"""Keyframe stage as a queue stage (pending → extracting → keyframes_ready)."""

from __future__ import annotations

from app import paths
from app.keyframes import KeyframeParams
from app.models import JobStatus
from app.pipeline import KeyframeWorker, run_keyframe_stage
from app.store import InMemoryJobStore


def test_worker_claims_pending_and_runs_processor():
    store = InMemoryJobStore()
    job = store.create_job()
    store.put_object(paths.video_key(job.id), b"vid", "video/mp4")
    store.update_job(job.id, video_path=paths.video_key(job.id))
    seen = []

    def proc(s, j, params):
        seen.append((j.id, j.status))
        s.update_job(j.id, status=JobStatus.KEYFRAMES_READY)

    w = KeyframeWorker(store, KeyframeParams(), processor=proc)
    assert w.run_once() is True
    assert seen == [(job.id, JobStatus.EXTRACTING)]
    assert store.get_job(job.id).status == JobStatus.KEYFRAMES_READY
    assert w.run_once() is False


def test_stage_without_video_fails_with_patient_message():
    store = InMemoryJobStore()
    job = store.create_job()
    store.update_job(job.id, status=JobStatus.EXTRACTING)
    run_keyframe_stage(store, job.id, KeyframeParams())
    failed = store.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_stage == "extract"
    assert failed.error == "We couldn't read that video. Please record it again."
    assert "no uploaded video" in failed.error_detail


def test_stage_with_garbage_video_fails_cleanly(tmp_path):
    """ffmpeg (if installed) rejects junk bytes → failed, never stuck in extracting."""
    import shutil

    if shutil.which("ffprobe") is None:
        import pytest

        pytest.skip("ffmpeg not installed")
    store = InMemoryJobStore()
    job = store.create_job()
    store.put_object(paths.video_key(job.id), b"not a video", "video/mp4")
    store.update_job(job.id, video_path=paths.video_key(job.id), status=JobStatus.EXTRACTING)
    run_keyframe_stage(store, job.id, KeyframeParams())
    failed = store.get_job(job.id)
    assert failed.status == JobStatus.FAILED and failed.error_stage == "extract"
    assert "ffprobe" not in failed.error and "ffmpeg" not in failed.error


def test_pending_job_is_not_claimable_until_video_is_stored():
    """POST /scans creates the row, then uploads the video (seconds). The worker
    must not grab it in between (first real upload failed exactly this way)."""
    store = InMemoryJobStore()
    job = store.create_job()
    w = KeyframeWorker(store, KeyframeParams(), processor=lambda *a: None)
    assert w.run_once() is False  # no video yet
    store.update_job(job.id, video_path=paths.video_key(job.id))
    assert w.run_once() is True
