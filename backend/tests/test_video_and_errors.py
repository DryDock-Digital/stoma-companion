"""Videos over the storage cap are re-encoded to fit; server errors keep CORS
headers and a plain message (the browser otherwise shows 'network error')."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import InMemoryJobStore
from app.video import VideoTooLarge, fit_video


def test_small_video_passes_through():
    f = fit_video(b"x" * 1000, "video/quicktime", max_bytes=2000)
    assert f.data == b"x" * 1000 and not f.transcoded and f.content_type == "video/quicktime"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_large_video_is_reencoded_under_cap(tmp_path):
    src = tmp_path / "big.mov"
    # 3 s of noisy 640x360 video at absurd bitrate → several MB
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-qp",
            "0",
            str(src),
        ],
        check=True,
    )
    data = src.read_bytes()
    cap = len(data) // 3
    f = fit_video(data, "video/quicktime", max_bytes=cap)
    assert f.transcoded and len(f.data) <= cap and f.content_type == "video/mp4"
    assert f.original_bytes == len(data)


def test_impossible_cap_raises_patient_message():
    with pytest.raises(VideoTooLarge) as ei:
        fit_video(b"y" * 5000, "video/mp4", max_bytes=1, crf_steps=())
    assert "shorter" in ei.value.user_message


def _client_with_raising_store():
    class Boom(InMemoryJobStore):
        def put_object(self, path, data, content_type):
            raise RuntimeError("storage upload failed 400: EntityTooLarge /internal/path")

    app = create_app(store=Boom(), settings=Settings(run_keyframe_worker=False))
    return TestClient(app, raise_server_exceptions=False)


def test_server_error_is_json_with_cors_headers():
    client = _client_with_raising_store()
    r = client.post(
        "/admin/scans",
        files={"video": ("m.mov", b"v", "video/quicktime")},
        headers={"Origin": "https://stomacompanion.netlify.app"},
    )
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "*"
    assert r.json()["detail"] == "Something went wrong. Please try again."
    assert "EntityTooLarge" not in r.text and "/internal" not in r.text


def test_too_large_video_is_413_with_message(monkeypatch):
    app = create_app(
        store=InMemoryJobStore(),
        settings=Settings(run_keyframe_worker=False, storage_object_max_mb=0),
    )
    monkeypatch.setattr(
        "app.routes.scans.fit_video",
        lambda *a, **k: (_ for _ in ()).throw(VideoTooLarge("too big")),
    )
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/scans",
        files={"video": ("m.mov", b"v" * 10, "video/quicktime")},
        headers={"Origin": "https://x"},
    )
    assert r.status_code == 413 and "shorter" in r.json()["detail"]
    assert r.headers.get("access-control-allow-origin") == "*"
