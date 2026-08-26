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

router = APIRouter(prefix="/scans", tags=["scans"])

_ACCEPTED_PREFIXES = ("video/",)
_ACCEPTED_TYPES = {"application/octet-stream", ""}


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("", status_code=201, response_model=ScanCreated)
async def create_scan(
    video: UploadFile = File(...),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> ScanCreated:
    content_type = video.content_type or ""
    if not (content_type.startswith(_ACCEPTED_PREFIXES) or content_type in _ACCEPTED_TYPES):
        raise HTTPException(415, f"Unsupported upload type: {content_type!r}; expected a video.")

    t0 = time.perf_counter()
    data = await video.read()
    receive_s = time.perf_counter() - t0
    if not data:
        raise HTTPException(400, "Empty upload.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_mb} MB limit.")

    # Carry every knob used for this run so it stays reproducible (FR-07 ring, marker
    # size, dialect, …) — downstream stages read the job, never the process env.
    job = store.create_job(
        config={
            "keyframe_interval_seconds": settings.keyframe_interval_seconds,
            "keyframe_max_frames": settings.keyframe_max_frames,
            **settings.measure_config(),
        }
    )

    video_key = paths.video_key(job.id)
    t1 = time.perf_counter()
    store.put_object(video_key, data, content_type or "video/quicktime")
    store_s = time.perf_counter() - t1
    store.update_job(job.id, video_path=video_key)
    # upload time is part of the ≤2 min budget (FR-11): record what the server saw
    store.patch_result(
        job.id,
        {
            "timings_s": {"upload": round(receive_s + store_s, 4)},
            "upload_bytes": len(data),
        },
    )
    return ScanCreated(id=job.id, status=JobStatus.PENDING)


@router.get("/{job_id}", response_model=ScanStatus)
async def get_scan(job_id: str, store: JobStore = Depends(get_store)) -> ScanStatus:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan not found.")
    return ScanStatus.from_job(job)
