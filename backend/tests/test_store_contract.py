"""One contract suite, both stores. The in-memory store is the reference
semantics; the Supabase store runs the same assertions when STOMA_TEST_SUPABASE=1
(with SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY set) so drift between the Python
mirror and the SQL RPCs shows up before production does."""

from __future__ import annotations

import os
import uuid

import pytest

from app.config import Settings
from app.models import JobStatus
from app.store import InMemoryJobStore, SupabaseJobStore


def _stores():
    yield pytest.param("memory", id="memory")
    if os.environ.get("STOMA_TEST_SUPABASE") == "1":
        yield pytest.param("supabase", id="supabase")


@pytest.fixture(params=list(_stores()))
def store(request):
    if request.param == "memory":
        return InMemoryJobStore()
    settings = Settings()
    if not settings.supabase_configured:
        pytest.skip("SUPABASE_* not configured")
    return SupabaseJobStore(settings)


def test_create_get_update(store):
    job = store.create_job(config={"grace_ring_mm": 3.0})
    assert job.status == JobStatus.PENDING and job.attempts == 0
    got = store.get_job(job.id)
    assert got is not None and got.config["grace_ring_mm"] == 3.0
    upd = store.update_job(job.id, status=JobStatus.KEYFRAMES_READY, keyframe_count=7)
    assert upd.status == JobStatus.KEYFRAMES_READY and upd.keyframe_count == 7
    assert store.get_job(str(uuid.uuid4())) is None


def test_generic_claim_and_attempts(store):
    job = store.create_job()
    c = store.claim_next_job(f"t-{uuid.uuid4()}", None, JobStatus.PENDING, JobStatus.EXTRACTING)
    # another test's rows may be older on a shared Supabase; find ours
    while c is not None and c.id != job.id:
        store.update_job(c.id, status=JobStatus.PENDING, worker_id=None, claimed_at=None)
        c = store.claim_next_job("t", None, JobStatus.PENDING, JobStatus.EXTRACTING)
    assert c is not None and c.status == JobStatus.EXTRACTING and c.attempts == 1
    assert c.claimed_at is not None


def test_patch_result_merges(store):
    job = store.create_job()
    store.patch_result(job.id, {"timings_s": {"upload": 1.5}, "x": 1})
    r = store.patch_result(job.id, {"timings_s": {"extract": 2.5}, "y": [1, 2]}).result
    assert r["timings_s"] == {"upload": 1.5, "extract": 2.5}
    assert r["x"] == 1 and r["y"] == [1, 2]


def test_objects_round_trip_and_list_beyond_100(store):
    prefix = f"contract-{uuid.uuid4()}/keyframes/"
    n = 120  # > Supabase's default list page of 100
    for i in range(n):
        store.put_object(f"{prefix}frame_{i:05d}.jpg", b"x", "image/jpeg")
    keys = store.list_objects(prefix)
    assert len(keys) == n
    assert keys[0].endswith("frame_00000.jpg") and keys[-1].endswith("frame_00119.jpg")
    assert store.get_object(keys[0]) == b"x"


def test_queue_stats_shape(store):
    stats = store.queue_stats()
    assert set(stats) == {"counts", "oldest_claim_age_s"}
    assert isinstance(stats["counts"], dict)
