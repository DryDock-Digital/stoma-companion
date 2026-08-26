"""Persistence for jobs + objects, behind one protocol so the API, keyframe
extractor and queue poller never import Supabase directly.

Two implementations:
  - InMemoryJobStore: dev + tests, no external services.
  - SupabaseJobStore: production, backed by the jobs table + `scans` bucket.

Both expose the same surface, so tests exercise the real code paths.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from .config import Settings
from .models import Job, JobStatus


@runtime_checkable
class JobStore(Protocol):
    # --- jobs table ---
    def create_job(self, *, config: dict[str, Any] | None = None) -> Job: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def update_job(self, job_id: str, **fields: Any) -> Job: ...
    def claim_next_job(self, worker_id: str, engine: str) -> Job | None: ...

    # --- object storage (`scans` bucket) ---
    def put_object(self, path: str, data: bytes, content_type: str) -> str: ...
    def get_object(self, path: str) -> bytes: ...
    def list_objects(self, prefix: str) -> list[str]: ...
    def signed_url(self, path: str, expires_in: int = 3600) -> str: ...


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryJobStore:
    """Thread-safe in-memory store. Used by tests and local dev; also the
    reference semantics the SQL migration must match (see claim_next_job)."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._objects: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def create_job(self, *, config: dict[str, Any] | None = None) -> Job:
        with self._lock:
            job_id = str(uuid.uuid4())
            now = _now()
            job = Job(
                id=job_id,
                status=JobStatus.PENDING,
                config=config or {},
                created_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = job
            return job.model_copy(deep=True)

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def update_job(self, job_id: str, **fields: Any) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            updated = job.model_copy(update={**fields, "updated_at": _now()})
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def claim_next_job(self, worker_id: str, engine: str) -> Job | None:
        """Atomically flip the oldest keyframes_ready job to reconstructing.
        Mirrors the claim_next_job() SQL RPC (FOR UPDATE SKIP LOCKED)."""
        with self._lock:
            claimable = [j for j in self._jobs.values() if j.status == JobStatus.KEYFRAMES_READY]
            if not claimable:
                return None
            claimable.sort(key=lambda j: (j.created_at or _now(), j.id))
            job = claimable[0]
            updated = job.model_copy(
                update={
                    "status": JobStatus.RECONSTRUCTING,
                    "worker_id": worker_id,
                    "engine": engine,
                    "claimed_at": _now(),
                    "updated_at": _now(),
                }
            )
            self._jobs[job.id] = updated
            return updated.model_copy(deep=True)

    def put_object(self, path: str, data: bytes, content_type: str) -> str:
        with self._lock:
            self._objects[path] = data
        return path

    def get_object(self, path: str) -> bytes:
        with self._lock:
            if path not in self._objects:
                raise KeyError(path)
            return self._objects[path]

    def list_objects(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(p for p in self._objects if p.startswith(prefix))

    def signed_url(self, path: str, expires_in: int = 3600) -> str:
        # No server in-memory; return a stable pseudo-URL for tests/logging.
        return f"memory://{path}?expires_in={expires_in}"


class SupabaseJobStore:
    """Production store: jobs table + `scans` storage bucket via supabase-py.

    Constructed lazily (see build_store) so importing the app never requires
    Supabase credentials — tests and CI use the in-memory store instead.
    """

    def __init__(self, settings: Settings) -> None:
        from supabase import create_client  # imported here to keep it optional

        self._bucket = settings.supabase_storage_bucket
        self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _table(self):
        return self._client.table("jobs")

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        return Job.model_validate(row)

    def create_job(self, *, config: dict[str, Any] | None = None) -> Job:
        res = self._table().insert({"config": config or {}}).execute()
        return self._row_to_job(res.data[0])

    def get_job(self, job_id: str) -> Job | None:
        res = self._table().select("*").eq("id", job_id).limit(1).execute()
        return self._row_to_job(res.data[0]) if res.data else None

    def update_job(self, job_id: str, **fields: Any) -> Job:
        payload = _serialize_fields(fields)
        res = self._table().update(payload).eq("id", job_id).execute()
        if not res.data:
            raise KeyError(job_id)
        return self._row_to_job(res.data[0])

    def claim_next_job(self, worker_id: str, engine: str) -> Job | None:
        res = self._client.rpc(
            "claim_next_job", {"p_worker_id": worker_id, "p_engine": engine}
        ).execute()
        return self._row_to_job(res.data[0]) if res.data else None

    def put_object(self, path: str, data: bytes, content_type: str) -> str:
        self._client.storage.from_(self._bucket).upload(
            path, data, {"content-type": content_type, "upsert": "true"}
        )
        return path

    def get_object(self, path: str) -> bytes:
        return self._client.storage.from_(self._bucket).download(path)

    def list_objects(self, prefix: str) -> list[str]:
        folder = prefix.rstrip("/")
        items = self._client.storage.from_(self._bucket).list(folder)
        return sorted(f"{folder}/{it['name']}" for it in items)

    def signed_url(self, path: str, expires_in: int = 3600) -> str:
        res = self._client.storage.from_(self._bucket).create_signed_url(path, expires_in)
        return res["signedURL"]


def _serialize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Coerce enums/datetimes to JSON-friendly values for the DB client."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, JobStatus):
            out[key] = value.value
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def build_store(settings: Settings) -> JobStore:
    """Pick the store implementation from configuration."""
    if settings.supabase_configured:
        return SupabaseJobStore(settings)
    return InMemoryJobStore()
