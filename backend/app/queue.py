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
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from . import paths
from .cycle_time import StageTimer, merge_timing
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


# A measurement hook turns a finished reconstruction into the job `result` dict
# (P1-10). It runs on the worker while the mesh + keyframes + COLMAP poses are still
# local: (job, mesh_path, keyframe_dir, work_dir) -> result | None. When it returns a
# result the job goes straight to `done`; without a hook the worker stops at
# `mesh_ready` (measurement handled elsewhere).
MeasureHook = Callable[[Job, Path, Path, Path], dict | None]


class ReconstructionWorker:
    """Polls the queue and drives one job at a time through a `Reconstructor`,
    optionally running measurement inline (P1-10)."""

    def __init__(
        self,
        store: JobStore,
        reconstructor: Reconstructor,
        worker_id: str,
        measure_hook: MeasureHook | None = None,
    ) -> None:
        self.store = store
        self.reconstructor = reconstructor
        self.worker_id = worker_id
        self.measure_hook = measure_hook

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
                timer = StageTimer()
                with timer.stage("reconstruct"):
                    mesh_path = self.reconstructor.reconstruct(keyframe_dir, work_dir)

                mesh_key = paths.mesh_key(job.id)
                self.store.put_object(mesh_key, Path(mesh_path).read_bytes(), "model/obj")

                # Measurement (P1-10) runs here while mesh + keyframes + COLMAP poses
                # are still local. Without a hook the worker stops at mesh_ready.
                measure_result = None
                if self.measure_hook is not None:
                    self.store.update_job(job.id, status=JobStatus.MEASURING)
                    with timer.stage("measure"):
                        measure_result = self.measure_hook(
                            job, Path(mesh_path), keyframe_dir, work_dir
                        )

            # carry the per-stage cycle-time budget on the job (P2-6)
            result = merge_timing(job.result, "reconstruct", timer.get("reconstruct"))
            if measure_result is not None:
                result = merge_timing(result, "measure", timer.get("measure"))
                result.update(measure_result)
                self.store.update_job(
                    job.id, status=JobStatus.DONE, mesh_path=mesh_key, result=result
                )
                log.info("job %s -> done (measured, engine=%s)", job.id, self.reconstructor.name)
            else:
                self.store.update_job(
                    job.id, status=JobStatus.MESH_READY, mesh_path=mesh_key, result=result
                )
                log.info(
                    "job %s -> mesh_ready (engine=%s, %.1fs)",
                    job.id,
                    self.reconstructor.name,
                    timer.get("reconstruct"),
                )
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
