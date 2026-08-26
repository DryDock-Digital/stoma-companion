"""Reconstruction queue contract + reference poller (P1-3).

The contract, in one sentence: a worker claims a `keyframes_ready` job, downloads
its keyframes, produces a mesh OBJ, uploads it, and marks the job `mesh_ready` —
or `failed`. Nothing here knows or cares which engine produced the mesh; a
`Reconstructor` is any callable that turns a directory of keyframes into an OBJ.
That's what keeps COLMAP swappable for the Apple worker (decisions.md D3).

worker-colmap/ imports `ReconstructionWorker` and passes a COLMAP-backed
`Reconstructor`. A test passes a fake one. See docs/queue-contract.md.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Protocol

from . import paths
from .models import Job, JobStatus
from .store import JobStore

log = logging.getLogger(__name__)


class Reconstructor(Protocol):
    """images in → mesh out. The whole engine contract."""

    #: label written onto the job's `engine` column (e.g. 'colmap+openmvs').
    name: str

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> Path:
        """Return the path to an OBJ mesh built from the JPEGs in `keyframe_dir`.
        `work_dir` is a scratch space the engine owns for the call."""
        ...


class ReconstructionWorker:
    """Polls the queue and drives one job at a time through a `Reconstructor`."""

    def __init__(self, store: JobStore, reconstructor: Reconstructor, worker_id: str) -> None:
        self.store = store
        self.reconstructor = reconstructor
        self.worker_id = worker_id

    def claim(self) -> Job | None:
        """Atomically take the next reconstructable job (keyframes_ready →
        reconstructing). Returns None when the queue is empty."""
        return self.store.claim_next_job(self.worker_id, self.reconstructor.name)

    def process(self, job: Job) -> None:
        """Download keyframes, reconstruct, upload mesh, mark mesh_ready."""
        prefix = job.keyframes_prefix or paths.keyframes_prefix(job.id)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                keyframe_dir = tmp_dir / "keyframes"
                keyframe_dir.mkdir()

                keys = self.store.list_objects(prefix)
                frame_keys = [k for k in keys if Path(k).name.startswith("frame_")]
                if not frame_keys:
                    raise RuntimeError(f"no keyframes under {prefix}")
                for key in frame_keys:
                    (keyframe_dir / Path(key).name).write_bytes(self.store.get_object(key))

                work_dir = tmp_dir / "work"
                work_dir.mkdir()
                mesh_path = self.reconstructor.reconstruct(keyframe_dir, work_dir)

                mesh_key = paths.mesh_key(job.id)
                self.store.put_object(mesh_key, Path(mesh_path).read_bytes(), "model/obj")

            self.store.update_job(job.id, status=JobStatus.MESH_READY, mesh_path=mesh_key)
            log.info("job %s -> mesh_ready (engine=%s)", job.id, self.reconstructor.name)
        except Exception as exc:  # noqa: BLE001 — record the failure on the job
            log.exception("reconstruction failed for job %s", job.id)
            self.store.update_job(job.id, status=JobStatus.FAILED, error=str(exc))

    def run_once(self) -> bool:
        """Claim + process one job. Returns True if a job was handled."""
        job = self.claim()
        if job is None:
            return False
        self.process(job)
        return True

    def run_forever(
        self, poll_interval: float = 5.0, *, _max_idle_polls: int | None = None
    ) -> None:
        """Poll until interrupted. `_max_idle_polls` bounds idle polling for tests."""
        idle = 0
        log.info("worker %s polling (engine=%s)", self.worker_id, self.reconstructor.name)
        while True:
            did_work = self.run_once()
            if did_work:
                idle = 0
                continue
            idle += 1
            if _max_idle_polls is not None and idle >= _max_idle_polls:
                return
            time.sleep(poll_interval)
