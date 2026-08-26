from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import InMemoryJobStore


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def processor_calls() -> list:
    return []


@pytest.fixture
def client(store: InMemoryJobStore, processor_calls: list) -> TestClient:
    """App wired to the in-memory store with a stub processor (no ffmpeg), so the
    endpoint tests exercise upload/persist without shelling out."""
    app = create_app(store=store, settings=Settings())

    def stub_processor(s, job_id, params):
        processor_calls.append((job_id, params))

    app.state.processor = stub_processor
    return TestClient(app)
