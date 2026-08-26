"""Queue contract tests (P1-3): claim semantics + an end-to-end worker run
driven by a fake in-process reconstructor (no COLMAP)."""

from __future__ import annotations

from pathlib import Path

from app import paths
from app.models import JobStatus
from app.queue import ReconstructionWorker
from app.store import InMemoryJobStore


class FakeReconstructor:
    name = "fake-engine"

    def __init__(self) -> None:
        self.calls = 0

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> Path:
        self.calls += 1
        # A real engine reads the JPEGs; we just assert they were staged.
        assert list(keyframe_dir.glob("frame_*.jpg")), "keyframes not staged"
        mesh = work_dir / "mesh.obj"
        mesh.write_text("o stoma\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        return mesh


def _ready_job_with_keyframes(store: InMemoryJobStore, n: int = 8):
    job = store.create_job()
    for i in range(n):
        store.put_object(paths.keyframe_key(job.id, i), b"jpeg", "image/jpeg")
    store.update_job(
        job.id, status=JobStatus.KEYFRAMES_READY, keyframes_prefix=paths.keyframes_prefix(job.id)
    )
    return job


def test_claim_returns_none_when_empty():
    store = InMemoryJobStore()
    assert store.claim_next_job("w1", "fake") is None


def test_claim_flips_status_and_is_exclusive():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)

    claimed = store.claim_next_job("w1", "fake-engine")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RECONSTRUCTING
    assert claimed.worker_id == "w1"
    assert claimed.engine == "fake-engine"
    assert claimed.claimed_at is not None

    # already claimed → no second worker gets it
    assert store.claim_next_job("w2", "fake-engine") is None


def test_claim_takes_oldest_first():
    store = InMemoryJobStore()
    first = _ready_job_with_keyframes(store)
    second = _ready_job_with_keyframes(store)
    a = store.claim_next_job("w1", "fake-engine")
    b = store.claim_next_job("w2", "fake-engine")
    assert {a.id, b.id} == {first.id, second.id}
    assert a.id == first.id  # oldest first


def test_worker_processes_job_to_mesh_ready():
    store = InMemoryJobStore()
    job = _ready_job_with_keyframes(store)
    engine = FakeReconstructor()
    worker = ReconstructionWorker(store, engine, worker_id="w1")

    did_work = worker.run_once()
    assert did_work is True
    assert engine.calls == 1

    done = store.get_job(job.id)
    assert done.status == JobStatus.MESH_READY
    assert done.mesh_path == paths.mesh_key(job.id)
    assert store.get_object(done.mesh_path).startswith(b"o stoma")


def test_worker_marks_failed_when_no_keyframes():
    store = InMemoryJobStore()
    job = store.create_job()
    store.update_job(job.id, status=JobStatus.KEYFRAMES_READY)  # no objects uploaded
    worker = ReconstructionWorker(store, FakeReconstructor(), worker_id="w1")

    worker.run_once()
    failed = store.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert "no keyframes" in (failed.error or "")


def test_run_once_returns_false_when_idle():
    store = InMemoryJobStore()
    worker = ReconstructionWorker(store, FakeReconstructor(), worker_id="w1")
    assert worker.run_once() is False
