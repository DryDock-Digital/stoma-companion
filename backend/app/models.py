"""Job + API models. Mirrors supabase/migrations/0001_jobs.sql."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Scan lifecycle. See docs/queue-contract.md for the state machine."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    KEYFRAMES_READY = "keyframes_ready"
    RECONSTRUCTING = "reconstructing"
    MESH_READY = "mesh_ready"
    MEASURING = "measuring"
    MEASURED = "measured"
    CUTTING = "cutting"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    """One scan. Field names match the jobs table columns 1:1."""

    id: str
    status: JobStatus = JobStatus.PENDING
    video_path: str | None = None
    keyframes_prefix: str | None = None
    keyframe_count: int | None = None
    mesh_path: str | None = None
    engine: str | None = None
    worker_id: str | None = None
    claimed_at: datetime | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- API response shapes ---------------------------------------------------


class ScanCreated(BaseModel):
    id: str
    status: JobStatus


class ScanStatus(BaseModel):
    """What GET /scans/{id} returns. Deliberately excludes internal worker
    bookkeeping (worker_id/claimed_at) — the client only needs progress +
    result."""

    id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_job(cls, job: Job) -> ScanStatus:
        return cls(
            id=job.id,
            status=job.status,
            result=job.result,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
