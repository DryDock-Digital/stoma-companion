from __future__ import annotations

from fastapi.testclient import TestClient

from app import paths
from app.config import Settings
from app.main import create_app
from app.models import JobStatus
from app.store import InMemoryJobStore


def _upload(client, data=b"\x00\x01\x02fakevideo", content_type="video/mp4"):
    return client.post("/scans", files={"video": ("clip.mp4", data, content_type)})


def test_health_reports_queue(client, store):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["queue"] == {"counts": {}, "oldest_claim_age_s": None}
    _upload(client)
    body = client.get("/health").json()
    assert body["queue"]["counts"] == {"pending": 1}


def test_post_scan_creates_pending_job_and_stores_video(
    client, store, keyframe_worker, processor_calls
):
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
    # config carries the reproducibility knobs (incl. FR-07 grace ring + dialect)
    for key in ("grace_ring_mm", "marker_side_mm", "tolerance_mm", "gcode_dialect"):
        assert key in job.config
    # upload time is on the cycle budget
    assert "upload" in job.result["timings_s"]

    # the keyframe stage is a queue stage: nothing ran yet, the worker claims it
    assert processor_calls == []
    assert keyframe_worker.run_once() is True
    assert processor_calls[0][0] == job_id
    assert store.get_job(job_id).status == JobStatus.KEYFRAMES_READY
    assert store.get_job(job_id).attempts == 1
    assert keyframe_worker.run_once() is False


def test_get_scan_returns_status_without_internals(client, store):
    job_id = _upload(client).json()["id"]
    store.update_job(
        job_id,
        status=JobStatus.FAILED,
        error="Please try again.",
        error_detail="Traceback: /opt/colmap/... boom",
        error_stage="reconstruct",
        worker_id="w1",
    )
    body = client.get(f"/scans/{job_id}").json()
    assert body["id"] == job_id and body["status"] == "failed"
    assert body["error"] == "Please try again."
    for hidden in ("worker_id", "error_detail", "error_stage", "claimed_at"):
        assert hidden not in body


def test_gcode_is_a_path_not_a_blob(client, store):
    job_id = _upload(client).json()["id"]
    store.patch_result(job_id, {"diameter_mm": 33.0, "gcode": "G1 X1"})
    store.update_job(job_id, status=JobStatus.MEASURED, gcode_path=paths.gcode_key(job_id))
    body = client.get(f"/scans/{job_id}").json()
    assert "gcode" not in body["result"]
    assert body["result"]["gcode_path"] == paths.gcode_key(job_id)


def test_get_missing_scan_404(client):
    resp = client.get("/scans/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_empty_upload_rejected(client):
    assert _upload(client, data=b"").status_code == 400


def test_non_video_rejected(client):
    assert _upload(client, data=b"hello", content_type="text/plain").status_code == 415


def test_oversize_upload_rejected():
    app = create_app(
        store=InMemoryJobStore(), settings=Settings(max_upload_mb=0, run_keyframe_worker=False)
    )
    tiny = TestClient(app)
    resp = tiny.post("/scans", files={"video": ("c.mp4", b"anything", "video/mp4")})
    assert resp.status_code == 413
