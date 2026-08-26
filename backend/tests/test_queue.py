"""Queue contract tests (P1-3/P1-10): generic claim semantics, the widened
reconstructor contract (mesh + poses), inline + standalone measurement, failure
paths, and the stale-claim watchdog — all driven by fakes (no COLMAP)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app import paths
from app.errors import StageError
from app.measure import poses as poses_mod
from app.measure.orientation import PinholeCamera
from app.models import JobStatus
from app.queue import (
    CombinedWorker,
    MeasurementWorker,
    ReconstructionOutput,
    ReconstructionWorker,
)
from app.store import InMemoryJobStore


def _cam() -> PinholeCamera:
    return PinholeCamera.look_at((0, 0, 100.0), (0, 0, 0), image_size=(640, 480))


class FakeReconstructor:
    name = "fake-engine"

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls = 0
        self.fail_with = fail_with

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> ReconstructionOutput:
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        frames = sorted(keyframe_dir.glob("frame_*.jpg"))
        assert frames, "keyframes not staged"
        mesh = work_dir / "mesh.obj"
        mesh.write_text("o stoma\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        return ReconstructionOutput(
            mesh_path=mesh,
            cameras={f.name: _cam() for f in frames},
            diagnostics={"registered": len(frames)},
        )


class FakeMeasurer:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list = []
        self.fail_with = fail_with

    def measure(self, job, mesh_path, cameras, keyframe_dir):
        self.calls.append((job.id, mesh_path.exists(), len(cameras), keyframe_dir))
        if self.fail_with:
            raise self.fail_with
        return {"diameter_mm": 33.0, "within_tolerance": None, "gcode": "G21\nG1 X1 Y1\nM30\n"}


def _ready_job_with_keyframes(store: InMemoryJobStore, n: int = 8):
    job = store.create_job()
    for i in range(n):
        store.put_object(paths.keyframe_key(job.id, i), b"jpeg", "image/jpeg")
    store.update_job(
        job.id, status=JobStatus.KEYFRAMES_READY, keyframes_prefix=paths.keyframes_prefix(job.id)
    )
    return job


# --- claim -----------------------------------------------------------------


def test_claim_returns_none_when_empty():
    store = InMemoryJobStore()
    assert store.claim_next_job("w1", "fake") is None


def test_claim_flips_status_bumps_attempts_and_is_exclusive():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)

    claimed = store.claim_next_job("w1", "fake-engine")
    assert claimed is not None and claimed.id == job.id
    assert claimed.status == JobStatus.RECONSTRUCTING
    assert claimed.worker_id == "w1" and claimed.engine == "fake-engine"
    assert claimed.claimed_at is not None and claimed.attempts == 1
    assert store.claim_next_job("w2", "fake-engine") is None


def test_claim_is_generic_over_stages():
    store = InMemoryJobStore()
    job = store.create_job()
    store.update_job(job.id, video_path=paths.video_key(job.id))
    c = store.claim_next_job("k1", None, JobStatus.PENDING, JobStatus.EXTRACTING)
    assert c.id == job.id and c.status == JobStatus.EXTRACTING and c.engine is None
    store.update_job(job.id, status=JobStatus.MESH_READY, engine="mac")
    c = store.claim_next_job("m1", None, JobStatus.MESH_READY, JobStatus.MEASURING)
    assert c.status == JobStatus.MEASURING and c.engine == "mac"  # engine untouched
    assert c.attempts == 2


def test_claim_takes_oldest_first():
    store = InMemoryJobStore()
    first = _ready_job_with_keyframes(store)
    second = _ready_job_with_keyframes(store)
    a = store.claim_next_job("w1", "fake-engine")
    b = store.claim_next_job("w2", "fake-engine")
    assert {a.id, b.id} == {first.id, second.id}
    assert a.id == first.id


# --- reconstruction --------------------------------------------------------


def test_worker_processes_job_to_mesh_ready_with_poses():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    engine = FakeReconstructor()
    worker = ReconstructionWorker(store, engine, worker_id="w1")

    assert worker.run_once() is True
    assert engine.calls == 1

    done = store.get_job(job.id)
    assert done.status == JobStatus.MESH_READY
    assert done.mesh_path == paths.mesh_key(job.id)
    assert done.poses_path == paths.poses_key(job.id)
    import gzip

    assert done.mesh_path.endswith(".obj.gz")
    assert gzip.decompress(store.get_object(done.mesh_path)).startswith(b"o stoma")
    cams = poses_mod.loads(store.get_object(done.poses_path))
    assert len(cams) == 8 and isinstance(next(iter(cams.values())), PinholeCamera)
    timings = done.result["timings_s"]
    assert {"download", "reconstruct", "upload"} <= set(timings)
    assert done.result["engine"] == "fake-engine"
    assert done.result["registered_frames"] == 8


def test_worker_marks_failed_with_patient_safe_message_when_no_keyframes():
    store = InMemoryJobStore()
    job = store.create_job()
    store.update_job(job.id, status=JobStatus.KEYFRAMES_READY)
    ReconstructionWorker(store, FakeReconstructor(), worker_id="w1").run_once()
    failed = store.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_stage == "reconstruct"
    assert "no keyframes" in failed.error_detail
    assert "keyframes" not in failed.error  # patient text, not the raw message
    assert failed.error.endswith(".")


def test_engine_stage_error_message_is_used():
    store = InMemoryJobStore()
    _ready_job_with_keyframes(store)
    exc = StageError("colmap exit 3", stage="reconstruct", user_message="Please film again.")
    ReconstructionWorker(store, FakeReconstructor(fail_with=exc), worker_id="w1").run_once()
    failed = next(iter(store._jobs.values()))
    assert failed.error == "Please film again."
    assert "colmap exit 3" in failed.error_detail


def test_run_once_returns_false_when_idle():
    store = InMemoryJobStore()
    assert ReconstructionWorker(store, FakeReconstructor(), worker_id="w1").run_once() is False


# --- measurement -----------------------------------------------------------


def test_inline_measurement_drives_job_to_measured_with_gcode_object():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    measurer = FakeMeasurer()
    worker = ReconstructionWorker(store, FakeReconstructor(), worker_id="w1", measurer=measurer)
    assert worker.run_once() is True

    done = store.get_job(job.id)
    assert done.status == JobStatus.MEASURED  # not DONE — that's the cut (P4)
    assert done.mesh_path == paths.mesh_key(job.id)
    assert done.gcode_path == paths.gcode_key(job.id)
    assert store.get_object(done.gcode_path) == b"G21\nG1 X1 Y1\nM30\n"
    assert done.result["diameter_mm"] == 33.0
    assert "gcode" not in done.result  # never a JSON blob in the poll payload
    assert {"reconstruct", "measure"} <= set(done.result["timings_s"])
    assert measurer.calls[0][:3] == (job.id, True, 8)


def test_measure_failure_keeps_the_mesh_artifacts():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    worker = ReconstructionWorker(
        store, FakeReconstructor(), worker_id="w1", measurer=FakeMeasurer(RuntimeError("nan"))
    )
    worker.run_once()
    failed = store.get_job(job.id)
    assert failed.status == JobStatus.FAILED and failed.error_stage == "measure"
    assert failed.mesh_path and failed.poses_path  # a good mesh is never hidden by a bad measure
    assert store.get_object(failed.mesh_path)


def test_measurement_worker_picks_up_mesh_ready_from_another_engine(tmp_path):
    """A Mac worker (no measurer) leaves mesh_ready; the measurement worker
    finishes it from storage alone — that's what makes the fallback a drop-in."""
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    ReconstructionWorker(store, FakeReconstructor(), worker_id="mac-1").run_once()
    assert store.get_job(job.id).status == JobStatus.MESH_READY

    measurer = FakeMeasurer()
    mw = MeasurementWorker(store, measurer, worker_id="m1")
    assert mw.run_once() is True
    done = store.get_job(job.id)
    assert done.status == JobStatus.MEASURED and done.gcode_path
    assert measurer.calls[0][2] == 8  # cameras came from poses.json
    assert mw.run_once() is False


def test_combined_worker_runs_both_stages():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    orphan = _ready_job_with_keyframes(store)
    store.update_job(
        orphan.id,
        status=JobStatus.MESH_READY,
        mesh_path=paths.mesh_key(orphan.id),
        poses_path=paths.poses_key(orphan.id),
    )
    import gzip

    store.put_object(paths.mesh_key(orphan.id), gzip.compress(b"o x\n"), "application/gzip")
    store.put_object(
        paths.poses_key(orphan.id), poses_mod.dumps({"a": _cam()}).encode(), "application/json"
    )

    m = FakeMeasurer()
    cw = CombinedWorker(
        ReconstructionWorker(store, FakeReconstructor(), worker_id="w", measurer=m),
        MeasurementWorker(store, m, worker_id="w"),
    )
    cw.run_forever(poll_interval=0, _max_idle_polls=1)
    assert store.get_job(job.id).status == JobStatus.MEASURED
    assert store.get_job(orphan.id).status == JobStatus.MEASURED


# --- robustness ------------------------------------------------------------


def test_store_failure_while_recording_error_does_not_kill_worker():
    store = InMemoryJobStore()
    _ready_job_with_keyframes(store)
    calls = {"n": 0}
    real_update = store.update_job

    def flaky_update(job_id, **fields):
        calls["n"] += 1
        raise ConnectionError("supabase blip")

    store.update_job = flaky_update  # every write fails
    worker = ReconstructionWorker(store, FakeReconstructor(), worker_id="w1")
    assert worker.run_once() is True  # no exception escaped
    store.update_job = real_update
    assert calls["n"] >= 1


def test_watchdog_requeues_stale_claim_then_fails_it():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    claimed = store.claim_next_job("w1", "fake")
    # nothing stale yet
    assert store.requeue_stale_jobs(older_than_s=60, max_attempts=2) == []
    # age the claim
    store._jobs[job.id] = store._jobs[job.id].model_copy(
        update={"claimed_at": datetime.now(UTC) - timedelta(seconds=120)}
    )
    touched = store.requeue_stale_jobs(older_than_s=60, max_attempts=2)
    assert [t.id for t in touched] == [job.id]
    back = store.get_job(job.id)
    assert back.status == JobStatus.KEYFRAMES_READY and back.worker_id is None
    assert back.attempts == claimed.attempts == 1

    # second claim + second stall → failed for good with a patient-safe message
    store.claim_next_job("w2", "fake")
    store._jobs[job.id] = store._jobs[job.id].model_copy(
        update={"claimed_at": datetime.now(UTC) - timedelta(seconds=120)}
    )
    touched = store.requeue_stale_jobs(older_than_s=60, max_attempts=2)
    failed = store.get_job(job.id)
    assert failed.status == JobStatus.FAILED and failed.error_stage == "timeout"
    assert "longer than expected" in failed.error
    assert "2 attempts" in failed.error_detail


def test_poller_runs_watchdog_in_loop():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    store.claim_next_job("dead", "fake")
    store._jobs[job.id] = store._jobs[job.id].model_copy(
        update={"claimed_at": datetime.now(UTC) - timedelta(seconds=999)}
    )
    engine = FakeReconstructor()
    worker = ReconstructionWorker(store, engine, worker_id="w1", claim_timeout_s=60, max_attempts=3)
    worker.run_forever(poll_interval=0, _max_idle_polls=1)
    # the watchdog freed the job and this worker then processed it
    assert engine.calls == 1
    assert store.get_job(job.id).status == JobStatus.MESH_READY


def test_patch_result_merges_timings_across_stages():
    store = InMemoryJobStore()
    job = store.create_job()
    store.patch_result(job.id, {"timings_s": {"upload": 1.0}, "a": 1})
    store.patch_result(job.id, {"timings_s": {"extract": 2.0}, "b": 2})
    r = store.get_job(job.id).result
    assert r == {"timings_s": {"upload": 1.0, "extract": 2.0}, "a": 1, "b": 2}


def test_queue_stats_reports_oldest_claim():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    store.claim_next_job("w1", "fake")
    store._jobs[job.id] = store._jobs[job.id].model_copy(
        update={"claimed_at": datetime.now(UTC) - timedelta(seconds=30)}
    )
    stats = store.queue_stats()
    assert stats["counts"] == {"reconstructing": 1}
    assert 29 < stats["oldest_claim_age_s"] < 40


def test_poses_round_trip():
    cam = PinholeCamera(
        K=np.eye(3) * 500,
        R=np.eye(3),
        t=np.array([0, 0, 5.0]),
        dist=np.array([-0.1, 0.01, 0, 0, 0]),
        image_size=(640, 480),
    )
    back = poses_mod.loads(poses_mod.dumps({"f.jpg": cam}))["f.jpg"]
    assert np.allclose(back.K, cam.K) and np.allclose(back.t, cam.t)
    assert np.allclose(back.dist, cam.dist) and back.image_size == (640, 480)
    with pytest.raises(ValueError):
        poses_mod.loads('{"format": "other", "cameras": {}}')


def test_heartbeat_keeps_claim_fresh_during_slow_stage():
    """A slow (CPU) reconstruction must not be killed by the watchdog: the worker
    touches claimed_at while working (the first real video was failed at 30 min)."""
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)

    class Slow(FakeReconstructor):
        def reconstruct(self, keyframe_dir, work_dir):
            import time

            time.sleep(0.25)
            return super().reconstruct(keyframe_dir, work_dir)

    seen = []
    real = store.update_job

    def spy(job_id, **f):
        if list(f) == ["claimed_at"]:
            seen.append(f["claimed_at"])
        return real(job_id, **f)

    store.update_job = spy
    w = ReconstructionWorker(store, Slow(), worker_id="w1", heartbeat_s=0.05)
    assert w.run_once() is True
    assert len(seen) >= 2  # heartbeats happened while reconstructing
    assert store.get_job(job.id).attempts == 0  # reset on stage completion


def test_attempts_are_per_stage():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    ReconstructionWorker(store, FakeReconstructor(), worker_id="w1").run_once()
    assert store.get_job(job.id).attempts == 0
    MeasurementWorker(store, FakeMeasurer(), worker_id="m1").run_once()
    assert store.get_job(job.id).status == JobStatus.MEASURED
    assert store.get_job(job.id).attempts == 0


def test_worker_claims_pending_job_and_extracts_locally(monkeypatch):
    """No keyframe stage in between: the worker downloads the video, extracts frames
    on its own disk, reconstructs, measures, then archives the frames."""
    from app import keyframes as kf

    store = InMemoryJobStore()
    job = store.create_job(config={"keyframe_interval_seconds": 0.5, "keyframe_max_frames": 100})
    store.put_object(paths.video_key(job.id), b"fake-video", "video/mp4")
    store.update_job(job.id, video_path=paths.video_key(job.id))

    def fake_extract(video_path, out_dir, params, **kw):
        assert video_path.read_bytes() == b"fake-video"
        assert params.interval_seconds == 0.5
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for i in range(9):
            f = out_dir / f"frame_{i:05d}.jpg"
            f.write_bytes(b"jpg")
            frames.append(f)
        return kf.KeyframeResult(count=9, frame_paths=frames, calibration_path=None)

    monkeypatch.setattr(kf, "extract_keyframes", fake_extract)
    m = FakeMeasurer()
    w = ReconstructionWorker(store, FakeReconstructor(), worker_id="w1", measurer=m)
    assert w.run_once() is True
    done = store.get_job(job.id)
    assert done.status == JobStatus.MEASURED and done.keyframe_count == 9
    t = done.result["timings_s"]
    assert {"download", "extract", "reconstruct", "upload", "measure", "archive"} <= set(t)
    assert len(store.list_objects(paths.keyframes_prefix(job.id))) == 9  # archived after
    assert m.calls[0][2] == 9


def test_pending_without_video_is_not_claimed_by_worker():
    store = InMemoryJobStore()
    store.create_job()
    w = ReconstructionWorker(store, FakeReconstructor(), worker_id="w1")
    assert w.run_once() is False
