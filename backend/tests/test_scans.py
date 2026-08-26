from __future__ import annotations

from fastapi.testclient import TestClient

from app import paths
from app.config import Settings
from app.main import create_app
from app.store import InMemoryJobStore


def _upload(client, data=b"\x00\x01\x02fakevideo", content_type="video/mp4"):
    return client.post("/scans", files={"video": ("clip.mp4", data, content_type)})


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_post_scan_creates_pending_job_and_stores_video(client, store, processor_calls):
    data = b"pretend-this-is-a-video" * 10
    resp = _upload(client, data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    job_id = body["id"]

    job = store.get_job(job_id)
    assert job is not None
    assert job.video_path == paths.video_key(job_id)
    assert store.get_object(job.video_path) == data
    # config carries the reproducibility knobs (incl. FR-07 grace ring)
    assert "grace_ring_mm" in job.config

    # keyframe stage was queued exactly once
    assert len(processor_calls) == 1
    assert processor_calls[0][0] == job_id


def test_get_scan_returns_status(client):
    job_id = _upload(client).json()["id"]
    resp = client.get(f"/scans/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert body["status"] == "pending"
    # internal bookkeeping is not exposed to clients
    assert "worker_id" not in body


def test_get_missing_scan_404(client):
    resp = client.get("/scans/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_empty_upload_rejected(client):
    resp = _upload(client, data=b"")
    assert resp.status_code == 400


def test_non_video_rejected(client):
    resp = _upload(client, data=b"hello", content_type="text/plain")
    assert resp.status_code == 415


def test_oversize_upload_rejected(client):
    # A zero-MB cap app exercises the size-limit branch.
    app = create_app(store=InMemoryJobStore(), settings=Settings(max_upload_mb=0))
    app.state.processor = lambda *a, **k: None
    tiny = TestClient(app)
    resp = tiny.post("/scans", files={"video": ("c.mp4", b"anything", "video/mp4")})
    assert resp.status_code == 413
