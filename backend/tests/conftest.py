from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.keyframes import KeyframeParams
from app.main import create_app
from app.pipeline import KeyframeWorker
from app.store import InMemoryJobStore


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def processor_calls() -> list:
    return []


@pytest.fixture
def keyframe_worker(store: InMemoryJobStore, processor_calls: list) -> KeyframeWorker:
    """A keyframe worker with a stub processor (no ffmpeg) that records calls and
    advances the job like the real stage would."""
    from app import paths
    from app.models import JobStatus

    def stub_processor(s, job, params):
        processor_calls.append((job.id, params))
        s.update_job(
            job.id,
            status=JobStatus.KEYFRAMES_READY,
            keyframes_prefix=paths.keyframes_prefix(job.id),
            keyframe_count=0,
        )

    return KeyframeWorker(store, KeyframeParams(), processor=stub_processor)


@pytest.fixture
def client(store: InMemoryJobStore, keyframe_worker: KeyframeWorker) -> TestClient:
    """App wired to the in-memory store. The in-process worker thread is disabled;
    tests drive `keyframe_worker.run_once()` explicitly."""
    app = create_app(
        store=store, settings=Settings(run_keyframe_worker=False), keyframe_worker=keyframe_worker
    )
    return TestClient(app)
