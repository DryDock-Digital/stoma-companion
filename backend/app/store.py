"""Persistence for jobs + objects, behind one protocol so the API, keyframe
extractor and queue pollers never import Supabase directly.

Two implementations:
  - InMemoryJobStore: dev + tests, no external services.
  - SupabaseJobStore: production, backed by the jobs table + `scans` bucket.

Both expose the same surface and are run through the same contract test
(tests/test_store_contract.py; Supabase behind an env flag).
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from .config import Settings
from .models import IN_PROGRESS_TO_CLAIMABLE, Job, JobStatus


@runtime_checkable
class JobStore(Protocol):
    # --- jobs table ---
    def create_job(self, *, config: dict[str, Any] | None = None) -> Job: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def list_jobs(self, limit: int = 50) -> list[Job]: ...
    def delete_job(self, job_id: str) -> bool: ...
    def update_job(self, job_id: str, **fields: Any) -> Job: ...
    def patch_result(self, job_id: str, patch: dict[str, Any]) -> Job: ...
    def claim_next_job(
        self,
        worker_id: str,
        engine: str | None,
        from_status: JobStatus = JobStatus.KEYFRAMES_READY,
        to_status: JobStatus = JobStatus.RECONSTRUCTING,
    ) -> Job | None: ...
    def requeue_stale_jobs(self, older_than_s: float, max_attempts: int) -> list[Job]: ...
    def queue_stats(self) -> dict[str, Any]: ...

    # --- object storage (`scans` bucket) ---
    def put_object(self, path: str, data: bytes, content_type: str) -> str: ...
    def get_object(self, path: str) -> bytes: ...
    def list_objects(self, prefix: str) -> list[str]: ...
    def delete_objects(self, keys: list[str]) -> int: ...
    def signed_url(self, path: str, expires_in: int = 3600) -> str: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _merge_result(existing: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge, with `timings_s` merged one level deeper so stages that run in
    different processes never clobber each other's timings."""
    merged = dict(existing or {})
    for k, v in patch.items():
        if k == "timings_s":
            t = dict(merged.get("timings_s", {}))
            t.update(v or {})
            merged["timings_s"] = t
        else:
            merged[k] = v
    return merged


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

    def list_jobs(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda j: (j.created_at or _now(), j.id), reverse=True
            )
            return [j.model_copy(deep=True) for j in jobs[:limit]]

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    def update_job(self, job_id: str, **fields: Any) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            updated = job.model_copy(update={**fields, "updated_at": _now()})
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def patch_result(self, job_id: str, patch: dict[str, Any]) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            updated = job.model_copy(
                update={"result": _merge_result(job.result, patch), "updated_at": _now()}
            )
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def claim_next_job(
        self,
        worker_id: str,
        engine: str | None,
        from_status: JobStatus = JobStatus.KEYFRAMES_READY,
        to_status: JobStatus = JobStatus.RECONSTRUCTING,
    ) -> Job | None:
        """Atomically flip the oldest `from_status` job to `to_status`.
        Mirrors the claim_next_job() SQL RPC (FOR UPDATE SKIP LOCKED)."""
        with self._lock:
            claimable = [
                j
                for j in self._jobs.values()
                if j.status == from_status
                # a `pending` row exists before its video finishes uploading
                and (from_status != JobStatus.PENDING or j.video_path is not None)
            ]
            if not claimable:
                return None
            claimable.sort(key=lambda j: (j.created_at or _now(), j.id))
            job = claimable[0]
            update = {
                "status": to_status,
                "worker_id": worker_id,
                "claimed_at": _now(),
                "attempts": job.attempts + 1,
                "updated_at": _now(),
            }
            if engine is not None:
                update["engine"] = engine
            updated = job.model_copy(update=update)
            self._jobs[job.id] = updated
            return updated.model_copy(deep=True)

    def requeue_stale_jobs(self, older_than_s: float, max_attempts: int) -> list[Job]:
        """Return in-progress jobs whose claim is older than `older_than_s` to their
        claimable state (or fail them once `attempts` reached `max_attempts`)."""
        cutoff = _now() - timedelta(seconds=older_than_s)
        touched: list[Job] = []
        with self._lock:
            for job in list(self._jobs.values()):
                back = IN_PROGRESS_TO_CLAIMABLE.get(job.status)
                if back is None or job.claimed_at is None or job.claimed_at > cutoff:
                    continue
                if job.attempts >= max_attempts:
                    from .errors import DEFAULT_MESSAGES

                    update = {
                        "status": JobStatus.FAILED,
                        "error": DEFAULT_MESSAGES["timeout"],
                        "error_detail": (
                            f"stage {job.status.value} claimed by {job.worker_id} at "
                            f"{job.claimed_at.isoformat()} never completed "
                            f"({job.attempts} attempts)"
                        ),
                        "error_stage": "timeout",
                    }
                else:
                    update = {"status": back, "worker_id": None, "claimed_at": None}
                updated = job.model_copy(update={**update, "updated_at": _now()})
                self._jobs[job.id] = updated
                touched.append(updated.model_copy(deep=True))
        return touched

    def queue_stats(self) -> dict[str, Any]:
        with self._lock:
            counts: dict[str, int] = {}
            oldest: float | None = None
            now = _now()
            for job in self._jobs.values():
                counts[job.status.value] = counts.get(job.status.value, 0) + 1
                if job.status in IN_PROGRESS_TO_CLAIMABLE and job.claimed_at is not None:
                    age = (now - job.claimed_at).total_seconds()
                    oldest = age if oldest is None else max(oldest, age)
        return {"counts": counts, "oldest_claim_age_s": oldest}

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

    def delete_objects(self, keys: list[str]) -> int:
        with self._lock:
            return sum(1 for k in keys if self._objects.pop(k, None) is not None)

    def signed_url(self, path: str, expires_in: int = 3600) -> str:
        # No server in-memory; return a stable pseudo-URL for tests/logging.
        return f"memory://{path}?expires_in={expires_in}"


class SupabaseJobStore:
    """Production store: jobs table + `scans` storage bucket via supabase-py.

    Constructed lazily (see build_store) so importing the app never requires
    Supabase credentials — tests and CI use the in-memory store instead.
    """

    LIST_PAGE = 1000  # storage.list() pages; default is 100 → silently truncates

    def __init__(self, settings: Settings) -> None:
        from supabase import create_client  # imported here to keep it optional

        self._bucket = settings.supabase_storage_bucket
        self._url = settings.supabase_url
        self._key = settings.supabase_service_role_key
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

    def list_jobs(self, limit: int = 50) -> list[Job]:
        res = self._table().select("*").order("created_at", desc=True).limit(limit).execute()
        return [self._row_to_job(r) for r in (res.data or [])]

    def delete_job(self, job_id: str) -> bool:
        res = self._table().delete().eq("id", job_id).execute()
        return bool(res.data)

    def update_job(self, job_id: str, **fields: Any) -> Job:
        payload = _serialize_fields(fields)
        res = self._table().update(payload).eq("id", job_id).execute()
        if not res.data:
            raise KeyError(job_id)
        return self._row_to_job(res.data[0])

    def patch_result(self, job_id: str, patch: dict[str, Any]) -> Job:
        res = self._client.rpc(
            "patch_job_result", {"p_id": job_id, "p_patch": _jsonable(patch)}
        ).execute()
        if not res.data:
            raise KeyError(job_id)
        return self._row_to_job(res.data[0])

    def claim_next_job(
        self,
        worker_id: str,
        engine: str | None,
        from_status: JobStatus = JobStatus.KEYFRAMES_READY,
        to_status: JobStatus = JobStatus.RECONSTRUCTING,
    ) -> Job | None:
        res = self._client.rpc(
            "claim_next_job",
            {
                "p_worker_id": worker_id,
                "p_engine": engine,
                "p_from": from_status.value,
                "p_to": to_status.value,
            },
        ).execute()
        return self._row_to_job(res.data[0]) if res.data else None

    def requeue_stale_jobs(self, older_than_s: float, max_attempts: int) -> list[Job]:
        res = self._client.rpc(
            "requeue_stale_jobs",
            {"p_older_than_s": older_than_s, "p_max_attempts": max_attempts},
        ).execute()
        return [self._row_to_job(r) for r in (res.data or [])]

    def queue_stats(self) -> dict[str, Any]:
        res = self._client.rpc("queue_stats", {}).execute()
        row = res.data[0] if isinstance(res.data, list) and res.data else (res.data or {})
        return {
            "counts": row.get("counts") or {},
            "oldest_claim_age_s": row.get("oldest_claim_age_s"),
        }

    #: above this size, bypass supabase-py's HTTP/2 client (CPU-bound at ~40 KB/s on
    #: the first real 162 MB mesh) and stream the body over plain HTTP/1.1.
    LARGE_OBJECT_BYTES = 8 * 1024 * 1024

    def put_object(self, path: str, data: bytes, content_type: str) -> str:
        if len(data) >= self.LARGE_OBJECT_BYTES:
            self._put_large(path, data, content_type)
            return path
        self._client.storage.from_(self._bucket).upload(
            path, data, {"content-type": content_type, "upsert": "true"}
        )
        return path

    def _put_large(self, path: str, data: bytes, content_type: str) -> None:
        import httpx

        url = f"{self._url.rstrip('/')}/storage/v1/object/{self._bucket}/{path}"
        headers = {
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        with httpx.Client(http2=False, timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            res = client.post(url, content=data, headers=headers)
            if res.status_code >= 400:
                raise RuntimeError(f"storage upload failed {res.status_code}: {res.text[:300]}")

    def get_object(self, path: str) -> bytes:
        return self._client.storage.from_(self._bucket).download(path)

    def list_objects(self, prefix: str) -> list[str]:
        """All object keys under `prefix`, paging past Supabase's default 100-item
        limit (a 350-frame keyframe set would otherwise be silently truncated)."""
        folder = prefix.rstrip("/")
        names: list[str] = []
        offset = 0
        while True:
            items = self._client.storage.from_(self._bucket).list(
                folder, {"limit": self.LIST_PAGE, "offset": offset}
            )
            names.extend(it["name"] for it in items if it.get("name"))
            if len(items) < self.LIST_PAGE:
                break
            offset += self.LIST_PAGE
        return sorted(f"{folder}/{n}" for n in names)

    def delete_objects(self, keys: list[str]) -> int:
        n = 0
        for i in range(0, len(keys), 100):
            chunk = keys[i : i + 100]
            self._client.storage.from_(self._bucket).remove(chunk)
            n += len(chunk)
        return n

    def signed_url(self, path: str, expires_in: int = 3600) -> str:
        res = self._client.storage.from_(self._bucket).create_signed_url(path, expires_in)
        return res["signedURL"]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, JobStatus):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    try:  # numpy scalars without importing numpy
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except ImportError:  # pragma: no cover
        pass
    return value


def _serialize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Coerce enums/datetimes/numpy to JSON-friendly values for the DB client."""
    return {key: _jsonable(value) for key, value in fields.items()}


def build_store(settings: Settings) -> JobStore:
    """Pick the store implementation from configuration."""
    if settings.supabase_configured:
        return SupabaseJobStore(settings)
    return InMemoryJobStore()
