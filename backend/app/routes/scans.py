"""Scan endpoints (P1-1).

POST /scans      video upload → storage, job row `pending`, keyframe stage queued
GET  /scans/{id} status + result
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile

from .. import paths
from ..config import Settings
from ..keyframes import KeyframeParams
from ..models import JobStatus, ScanCreated, ScanStatus
from ..store import JobStore

router = APIRouter(prefix="/scans", tags=["scans"])

# Processor signature: (store, job_id, params) -> None. Injected so tests can
# swap the real keyframe stage (which shells out to ffmpeg) for a stub.
Processor = Callable[[JobStore, str, KeyframeParams], None]

_ACCEPTED_PREFIXES = ("video/",)
_ACCEPTED_TYPES = {"application/octet-stream", ""}


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_processor(request: Request) -> Processor:
    return request.app.state.processor


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("", status_code=201, response_model=ScanCreated)
async def create_scan(
    background: BackgroundTasks,
    video: UploadFile = File(...),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
    processor: Processor = Depends(get_processor),
) -> ScanCreated:
    content_type = video.content_type or ""
    if not (content_type.startswith(_ACCEPTED_PREFIXES) or content_type in _ACCEPTED_TYPES):
        raise HTTPException(415, f"Unsupported upload type: {content_type!r}; expected a video.")

    data = await video.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_mb} MB limit.")

    # Carry the knobs used for this run so it stays reproducible (FR-07 ring, etc.).
    job = store.create_job(
        config={
            "keyframe_interval_seconds": settings.keyframe_interval_seconds,
            "keyframe_max_frames": settings.keyframe_max_frames,
            "grace_ring_mm": settings.grace_ring_mm,
        }
    )

    video_key = paths.video_key(job.id)
    store.put_object(video_key, data, content_type or "video/quicktime")
    store.update_job(job.id, video_path=video_key)

    params = KeyframeParams(
        interval_seconds=settings.keyframe_interval_seconds,
        max_frames=settings.keyframe_max_frames,
    )
    background.add_task(processor, store, job.id, params)

    return ScanCreated(id=job.id, status=JobStatus.PENDING)


@router.get("/{job_id}", response_model=ScanStatus)
async def get_scan(job_id: str, store: JobStore = Depends(get_store)) -> ScanStatus:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan not found.")
    return ScanStatus.from_job(job)
