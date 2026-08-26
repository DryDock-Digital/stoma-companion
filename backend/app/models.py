"""Job + API models. Mirrors supabase/migrations/0001_jobs.sql (+0004)."""

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


#: Stages a worker holds a claim on, and where a stale claim goes back to. A worker
#: that dies mid-stage leaves the row in the "in-progress" state; the watchdog
#: (`JobStore.requeue_stale_jobs`) returns it to the claimable state so another
#: worker picks it up, until `attempts` exceeds the cap.
IN_PROGRESS_TO_CLAIMABLE: dict[JobStatus, JobStatus] = {
    JobStatus.EXTRACTING: JobStatus.PENDING,
    JobStatus.RECONSTRUCTING: JobStatus.KEYFRAMES_READY,
    JobStatus.MEASURING: JobStatus.MESH_READY,
    JobStatus.CUTTING: JobStatus.MEASURED,
}

#: Success terminals for a poller: the pipeline ends at `measured` until the cutting
#: stage (P4) exists; `done` means the wafer was cut.
TERMINAL_STATUSES = frozenset({JobStatus.MEASURED, JobStatus.DONE, JobStatus.FAILED})


class Job(BaseModel):
    """One scan. Field names match the jobs table columns 1:1."""

    id: str
    status: JobStatus = JobStatus.PENDING
    video_path: str | None = None
    keyframes_prefix: str | None = None
    keyframe_count: int | None = None
    mesh_path: str | None = None
    poses_path: str | None = None
    gcode_path: str | None = None
    engine: str | None = None
    worker_id: str | None = None
    claimed_at: datetime | None = None
    attempts: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    #: patient-safe sentence (shown by the app as-is)
    error: str | None = None
    #: raw exception text — server-side only, never returned by the API
    error_detail: str | None = None
    error_stage: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- API response shapes ---------------------------------------------------


class ScanCreated(BaseModel):
    id: str
    status: JobStatus


#: result keys that are internal (paths, raw diagnostics) and not for the client
_RESULT_PRIVATE_KEYS = ("gcode",)


class ScanStatus(BaseModel):
    """What GET /scans/{id} returns. Deliberately excludes internal worker
    bookkeeping (worker_id/claimed_at/error_detail) — the client only needs
    progress + result + a patient-safe error."""

    id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_job(cls, job: Job) -> ScanStatus:
        result = None
        if job.result is not None:
            result = {k: v for k, v in job.result.items() if k not in _RESULT_PRIVATE_KEYS}
            if job.gcode_path:
                result["gcode_path"] = job.gcode_path
        return cls(
            id=job.id,
            status=job.status,
            result=result,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
