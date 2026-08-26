"""Verification run log (P5-1).

One `RunRecord` per measured run — measurement vs caliper truth, deviation, pass/fail
vs ±1 mm, and the engine/config that produced it (FR-19/FR-20). Mirrors
supabase/migrations/0003_runs.sql. `RunStore` has an in-memory implementation (dev /
tests) and a Supabase one (production), same pattern as the job store.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .config import Settings


class RunRecord(BaseModel):
    id: str | None = None
    created_at: datetime | None = None
    job_id: str | None = None
    model_name: str
    video_ref: str | None = None
    reference_point: str | None = None
    metric: str = "diameter"
    truth_mm: float | None = None
    measured_mm: float
    deviation_mm: float | None = None
    abs_deviation_mm: float | None = None
    tolerance_mm: float = 1.0
    passed: bool | None = None
    engine: str | None = None
    method: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @classmethod
    def build(
        cls,
        *,
        model_name: str,
        measured_mm: float,
        truth_mm: float | None = None,
        tolerance_mm: float = 1.0,
        **fields: Any,
    ) -> RunRecord:
        """Construct a record, deriving deviation / |deviation| / pass from
        measured vs truth so those stay consistent everywhere."""
        deviation = None if truth_mm is None else measured_mm - truth_mm
        abs_dev = None if deviation is None else abs(deviation)
        passed = None if abs_dev is None else abs_dev <= tolerance_mm
        return cls(
            model_name=model_name,
            measured_mm=measured_mm,
            truth_mm=truth_mm,
            tolerance_mm=tolerance_mm,
            deviation_mm=deviation,
            abs_deviation_mm=abs_dev,
            passed=passed,
            **fields,
        )


@runtime_checkable
class RunStore(Protocol):
    def insert(self, record: RunRecord) -> RunRecord: ...
    def list(  # noqa: A003 — mirrors the store surface
        self, *, model_name: str | None = None, limit: int | None = None
    ) -> list[RunRecord]: ...
    def delete(self, run_id: str) -> None: ...
    def find_by_job(self, job_id: str) -> RunRecord | None: ...
    def update(self, record: RunRecord) -> RunRecord: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    def insert(self, record: RunRecord) -> RunRecord:
        with self._lock:
            stored = record.model_copy(
                update={
                    "id": record.id or str(uuid.uuid4()),
                    "created_at": record.created_at or datetime.now(UTC),
                }
            )
            self._runs[stored.id] = stored
            return stored.model_copy(deep=True)

    def list(self, *, model_name: str | None = None, limit: int | None = None) -> list[RunRecord]:
        with self._lock:
            runs = sorted(
                self._runs.values(), key=lambda r: (r.created_at or datetime.now(UTC), r.id or "")
            )
        if model_name is not None:
            runs = [r for r in runs if r.model_name == model_name]
        return [r.model_copy(deep=True) for r in (runs[:limit] if limit else runs)]

    def delete(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def find_by_job(self, job_id: str) -> RunRecord | None:
        with self._lock:
            hits = [r for r in self._runs.values() if r.job_id == job_id]
        if not hits:
            return None
        hits.sort(key=lambda r: r.created_at or datetime.now(UTC))
        return hits[-1].model_copy(deep=True)

    def update(self, record: RunRecord) -> RunRecord:
        if not record.id:
            raise ValueError("update needs a record id")
        with self._lock:
            self._runs[record.id] = record.model_copy(deep=True)
            return record.model_copy(deep=True)


class SupabaseRunStore:
    def __init__(self, settings: Settings) -> None:
        from supabase import create_client

        self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _table(self):
        return self._client.table("runs")

    def insert(self, record: RunRecord) -> RunRecord:
        payload = record.model_dump(exclude_none=True, mode="json")
        payload.pop("id", None)
        payload.pop("created_at", None)
        res = self._table().insert(payload).execute()
        return RunRecord.model_validate(res.data[0])

    def list(self, *, model_name: str | None = None, limit: int | None = None) -> list[RunRecord]:
        q = self._table().select("*").order("created_at")
        if model_name is not None:
            q = q.eq("model_name", model_name)
        if limit is not None:
            q = q.limit(limit)
        return [RunRecord.model_validate(row) for row in q.execute().data]

    def delete(self, run_id: str) -> None:
        self._table().delete().eq("id", run_id).execute()

    def find_by_job(self, job_id: str) -> RunRecord | None:
        res = (
            self._table()
            .select("*")
            .eq("job_id", job_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return RunRecord.model_validate(res.data[0]) if res.data else None

    def update(self, record: RunRecord) -> RunRecord:
        if not record.id:
            raise ValueError("update needs a record id")
        payload = record.model_dump(mode="json", exclude={"id", "created_at"})
        res = self._table().update(payload).eq("id", record.id).execute()
        return RunRecord.model_validate(res.data[0]) if res.data else record


def build_run_store(settings: Settings) -> RunStore:
    if settings.supabase_configured:
        return SupabaseRunStore(settings)
    return InMemoryRunStore()
