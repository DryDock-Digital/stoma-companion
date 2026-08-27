"""Scan endpoints (P1-1).

POST /scans      video upload → storage, job row `pending` (the keyframe worker claims it)
GET  /scans/{id} status + result (patient-safe error text only)
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from .. import paths
from ..config import Settings
from ..models import JobStatus, ScanCreated, ScanStatus
from ..store import JobStore
from ..video import fit_video

router = APIRouter(prefix="/scans", tags=["scans"])

_ACCEPTED_PREFIXES = ("video/",)
_ACCEPTED_TYPES = {"application/octet-stream", ""}


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def read_and_validate_upload(video: UploadFile, settings: Settings) -> tuple[bytes, str, float]:
    """(bytes, content_type, seconds spent receiving) or an HTTPException."""
    content_type = video.content_type or ""
    if not (content_type.startswith(_ACCEPTED_PREFIXES) or content_type in _ACCEPTED_TYPES):
        raise HTTPException(415, f"Unsupported upload type: {content_type!r}; expected a video.")
    t0 = time.perf_counter()
    data = video.file.read()
    receive_s = time.perf_counter() - t0
    if not data:
        raise HTTPException(400, "Empty upload.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_mb} MB limit.")
    return data, content_type or "video/quicktime", receive_s


def store_video(store: JobStore, job_id: str, data: bytes, content_type: str, settings: Settings):
    """Fit the video under the storage cap, store it, record sizes. Returns the
    result patch (timings/sizes) for the caller to merge."""
    try:
        t0 = time.perf_counter()
        fitted = fit_video(data, content_type, max_bytes=settings.storage_object_max_bytes)
        fit_s = time.perf_counter() - t0
        video_key = paths.video_key(job_id)
        t1 = time.perf_counter()
        store.put_object(video_key, fitted.data, fitted.content_type)
        store_s = time.perf_counter() - t1
        store.update_job(job_id, video_path=video_key)
    except Exception:
        # no ghost rows: a job without a video can never run
        try:
            store.delete_job(job_id)
        except Exception:  # noqa: BLE001
            pass
        raise
    patch = {
        "upload_bytes": fitted.original_bytes,
        "stored_bytes": len(fitted.data),
        "video_transcoded": fitted.transcoded,
        "timings_s": {"store": round(store_s, 4)},
    }
    if fitted.transcoded:
        patch["timings_s"]["transcode"] = round(fit_s, 4)
        patch["video_crf"] = fitted.crf
    return patch


@router.post("", status_code=201, response_model=ScanCreated)
def create_scan(
    video: UploadFile = File(...),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> ScanCreated:
    data, content_type, receive_s = read_and_validate_upload(video, settings)

    # Carry every knob used for this run so it stays reproducible (FR-07 ring, marker
    # size, dialect, …) — downstream stages read the job, never the process env.
    job = store.create_job(
        config={
            "keyframe_interval_seconds": settings.keyframe_interval_seconds,
            "keyframe_max_frames": settings.keyframe_max_frames,
            "keyframe_target_frames": settings.keyframe_target_frames,
            **settings.measure_config(),
        }
    )
    patch = store_video(store, job.id, data, content_type, settings)
    # upload time is part of the ≤2 min budget (FR-11): record what the server saw
    patch["timings_s"]["upload"] = round(receive_s, 4)
    store.patch_result(job.id, patch)
    return ScanCreated(id=job.id, status=JobStatus.PENDING)


@router.get("/{job_id}", response_model=ScanStatus)
def get_scan(job_id: str, store: JobStore = Depends(get_store)) -> ScanStatus:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan not found.")
    return ScanStatus.from_job(job)
