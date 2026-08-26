"""Reconstruction queue contract + reference pollers (P1-3, P1-10).

The contract, in one sentence: a worker claims a `keyframes_ready` job, downloads
its keyframes, produces **a mesh OBJ plus the camera poses of the keyframes it
used** (engine-neutral, `measure/poses.py`), uploads both, and marks the job
`mesh_ready` — or `failed`. Nothing here knows or cares which engine produced the
mesh; a `Reconstructor` is any object that turns a directory of keyframes into a
`ReconstructionOutput`. That's what keeps COLMAP swappable for the Apple worker
(decisions.md D3): the measurement stage triangulates the marker from the poses,
never from an engine's own files.

Measurement (P1-10) is a second stage. It runs inline on the reconstruction worker
when a `Measurer` is attached (the mesh + keyframes are already local — fastest),
*and* `MeasurementWorker` claims any `mesh_ready` job left by a worker without
one (e.g. the Mac fallback). Either way the job ends at `measured`; `done` is the
cutting stage's (P4).

Robustness: every claim bumps `attempts`; `run_forever` periodically calls the
store's stale-claim watchdog so a worker that dies mid-job does not strand it;
failures write a patient-safe `error` + raw `error_detail` and never crash the
loop. worker-colmap/ imports these and passes a COLMAP-backed `Reconstructor`.
A test passes a fake one. See docs/queue-contract.md.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from . import paths
from .cycle_time import StageTimer
from .errors import failure_fields
from .models import Job, JobStatus
from .store import JobStore

if TYPE_CHECKING:  # numpy lives in the `measure` extra; the lean API image lacks it
    from .measure.orientation import PinholeCamera

log = logging.getLogger(__name__)


@dataclass
class ReconstructionOutput:
    """What an engine hands back: the mesh and the poses of the keyframes it used,
    both in the same (engine-unit) coordinate frame. `cameras` maps keyframe file
    name → PinholeCamera; frames the engine couldn't register are simply absent."""

    mesh_path: Path
    cameras: dict[str, PinholeCamera] = field(default_factory=dict)
    #: engine-specific extras for the run log (versions, image counts, …)
    diagnostics: dict = field(default_factory=dict)


class Reconstructor(Protocol):
    """keyframe images in → mesh + poses out. The whole engine contract."""

    #: label written onto the job's `engine` column (e.g. 'colmap+openmvs').
    name: str

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> ReconstructionOutput:
        """Build a mesh from the JPEGs in `keyframe_dir`; `work_dir` is scratch
        space the engine owns for the call. Must raise on failure (never return a
        missing mesh) and must respect its own wall-clock timeout."""
        ...


class Measurer(Protocol):
    """The measurement stage: (job, mesh, cameras, keyframe_dir) → result dict.
    Implemented by `measure_stage.MeasureStage`; a test passes a stub."""

    def measure(
        self, job: Job, mesh_path: Path, cameras: dict[str, PinholeCamera], keyframe_dir: Path
    ) -> dict: ...


def _download_keyframes(store: JobStore, job: Job, keyframe_dir: Path) -> list[str]:
    prefix = job.keyframes_prefix or paths.keyframes_prefix(job.id)
    keys = store.list_objects(prefix)
    frame_keys = [k for k in keys if Path(k).name.startswith("frame_")]
    if not frame_keys:
        raise RuntimeError(f"no keyframes under {prefix}")
    for key in frame_keys:
        (keyframe_dir / Path(key).name).write_bytes(store.get_object(key))
    return frame_keys


def _fail(store: JobStore, job_id: str, exc: BaseException, stage: str) -> None:
    """Record a failure on the job without letting a store error kill the worker."""
    log.exception("%s failed for job %s", stage, job_id)
    try:
        store.update_job(job_id, status=JobStatus.FAILED, **failure_fields(exc, stage))
    except Exception:  # noqa: BLE001
        log.exception("could not record failure for job %s", job_id)


class _Poller:
    """Shared claim/poll loop with the stale-claim watchdog."""

    def __init__(
        self,
        store: JobStore,
        worker_id: str,
        *,
        claim_timeout_s: float = 1800.0,
        max_attempts: int = 2,
        watchdog_every_s: float = 60.0,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.claim_timeout_s = claim_timeout_s
        self.max_attempts = max_attempts
        self.watchdog_every_s = watchdog_every_s
        self._last_watchdog = 0.0

    def run_once(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def watchdog(self) -> list[Job]:
        """Requeue (or fail) stale claims. Safe to call often; cheap when idle."""
        try:
            touched = self.store.requeue_stale_jobs(self.claim_timeout_s, self.max_attempts)
        except Exception:  # noqa: BLE001
            log.exception("stale-job watchdog failed")
            return []
        for job in touched:
            log.warning("watchdog: job %s -> %s (attempts=%d)", job.id, job.status, job.attempts)
        return touched

    def run_forever(
        self, poll_interval: float = 5.0, *, _max_idle_polls: int | None = None
    ) -> None:
        """Poll until interrupted. `_max_idle_polls` bounds idle polling for tests."""
        idle = 0
        log.info("%s %s polling", type(self).__name__, self.worker_id)
        while True:
            now = time.monotonic()
            if now - self._last_watchdog >= self.watchdog_every_s:
                self._last_watchdog = now
                self.watchdog()
            try:
                did_work = self.run_once()
            except Exception:  # noqa: BLE001 — never let one job kill the loop
                log.exception("worker loop error")
                did_work = False
            if did_work:
                idle = 0
                continue
            idle += 1
            if _max_idle_polls is not None and idle >= _max_idle_polls:
                return
            time.sleep(poll_interval)


class ReconstructionWorker(_Poller):
    """Polls the queue and drives one job at a time through a `Reconstructor`,
    then (if a `Measurer` is attached) straight through measurement."""

    def __init__(
        self,
        store: JobStore,
        reconstructor: Reconstructor,
        worker_id: str,
        measurer: Measurer | None = None,
        **poller_kwargs,
    ) -> None:
        super().__init__(store, worker_id, **poller_kwargs)
        self.reconstructor = reconstructor
        self.measurer = measurer

    def claim(self) -> Job | None:
        """Atomically take the next reconstructable job (keyframes_ready →
        reconstructing). Returns None when the queue is empty."""
        return self.store.claim_next_job(
            self.worker_id,
            self.reconstructor.name,
            JobStatus.KEYFRAMES_READY,
            JobStatus.RECONSTRUCTING,
        )

    def process(self, job: Job) -> None:
        """Download keyframes, reconstruct, upload mesh + poses, mark mesh_ready;
        then measure inline when a measurer is attached."""
        stage = "reconstruct"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                keyframe_dir = tmp_dir / "keyframes"
                keyframe_dir.mkdir()
                work_dir = tmp_dir / "work"
                work_dir.mkdir()
                timer = StageTimer()

                with timer.stage("download"):
                    _download_keyframes(self.store, job, keyframe_dir)
                with timer.stage("reconstruct"):
                    out = self.reconstructor.reconstruct(keyframe_dir, work_dir)
                if not Path(out.mesh_path).exists():
                    raise RuntimeError("engine returned a mesh path that does not exist")

                # Upload artefacts and record them *before* anything else can fail,
                # so a measurement error never hides a good mesh.
                from .measure import poses as poses_mod

                mesh_key = paths.mesh_key(job.id)
                poses_key = paths.poses_key(job.id)
                with timer.stage("upload"):
                    self.store.put_object(mesh_key, Path(out.mesh_path).read_bytes(), "model/obj")
                    self.store.put_object(
                        poses_key, poses_mod.dumps(out.cameras).encode(), "application/json"
                    )
                self.store.update_job(
                    job.id,
                    status=JobStatus.MESH_READY,
                    mesh_path=mesh_key,
                    poses_path=poses_key,
                )
                self.store.patch_result(
                    job.id,
                    {
                        "timings_s": timer.as_dict(),
                        "engine": self.reconstructor.name,
                        "reconstruction": out.diagnostics,
                        "registered_frames": len(out.cameras),
                    },
                )
                log.info(
                    "job %s -> mesh_ready (engine=%s, %.1fs, %d posed frames)",
                    job.id,
                    self.reconstructor.name,
                    timer.get("reconstruct"),
                    len(out.cameras),
                )

                if self.measurer is None:
                    return
                # inline measurement: claim the mesh_ready → measuring hop ourselves
                stage = "measure"
                claimed = self.store.claim_next_job(
                    self.worker_id, None, JobStatus.MESH_READY, JobStatus.MEASURING
                )
                if claimed is None or claimed.id != job.id:
                    # someone else (a MeasurementWorker) got there first — fine
                    return
                measure_job(
                    self.store,
                    claimed,
                    self.measurer,
                    Path(out.mesh_path),
                    out.cameras,
                    keyframe_dir,
                )
        except Exception as exc:  # noqa: BLE001 — record the failure on the job
            _fail(self.store, job.id, exc, stage)

    def run_once(self) -> bool:
        """Claim + process one job. Returns True if a job was handled."""
        job = self.claim()
        if job is None:
            return False
        self.process(job)
        return True


def measure_job(
    store: JobStore,
    job: Job,
    measurer: Measurer,
    mesh_path: Path,
    cameras: dict[str, PinholeCamera],
    keyframe_dir: Path,
) -> dict:
    """Run the measurement stage on a job already in `measuring`; ends at
    `measured` (or raises for the caller to record the failure)."""
    timer = StageTimer()
    with timer.stage("measure"):
        result = measurer.measure(job, mesh_path, cameras, keyframe_dir)
    gcode_text = result.pop("gcode", None)
    update: dict = {"status": JobStatus.MEASURED}
    if gcode_text:
        gcode_key = paths.gcode_key(job.id)
        store.put_object(gcode_key, gcode_text.encode(), "text/plain")
        update["gcode_path"] = gcode_key
    store.patch_result(job.id, {**result, "timings_s": timer.as_dict()})
    store.update_job(job.id, **update)
    log.info("job %s -> measured (%.1fs)", job.id, timer.get("measure"))
    return result


class MeasurementWorker(_Poller):
    """Claims `mesh_ready` jobs (mesh + poses in storage, from *any* engine) and
    runs the measurement stage. This is what makes the Mac fallback worker a true
    drop-in: it only has to produce a mesh and poses."""

    def __init__(self, store: JobStore, measurer: Measurer, worker_id: str, **poller_kwargs):
        super().__init__(store, worker_id, **poller_kwargs)
        self.measurer = measurer

    def claim(self) -> Job | None:
        return self.store.claim_next_job(
            self.worker_id, None, JobStatus.MESH_READY, JobStatus.MEASURING
        )

    def process(self, job: Job) -> None:
        from .measure import poses as poses_mod

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                keyframe_dir = tmp_dir / "keyframes"
                keyframe_dir.mkdir()
                timer = StageTimer()
                with timer.stage("download"):
                    _download_keyframes(self.store, job, keyframe_dir)
                    mesh_path = tmp_dir / "mesh.obj"
                    mesh_path.write_bytes(
                        self.store.get_object(job.mesh_path or paths.mesh_key(job.id))
                    )
                    cameras = poses_mod.loads(
                        self.store.get_object(job.poses_path or paths.poses_key(job.id))
                    )
                self.store.patch_result(
                    job.id, {"timings_s": {"download_measure": timer.get("download")}}
                )
                measure_job(self.store, job, self.measurer, mesh_path, cameras, keyframe_dir)
        except Exception as exc:  # noqa: BLE001
            _fail(self.store, job.id, exc, "measure")

    def run_once(self) -> bool:
        job = self.claim()
        if job is None:
            return False
        self.process(job)
        return True


class CombinedWorker(_Poller):
    """One process, both stages: reconstruction first, then any orphaned
    `mesh_ready` jobs. What `worker-colmap/worker.py` runs."""

    def __init__(self, reconstruction: ReconstructionWorker, measurement: MeasurementWorker):
        super().__init__(
            reconstruction.store,
            reconstruction.worker_id,
            claim_timeout_s=reconstruction.claim_timeout_s,
            max_attempts=reconstruction.max_attempts,
        )
        self.reconstruction = reconstruction
        self.measurement = measurement

    def run_once(self) -> bool:
        return self.reconstruction.run_once() or self.measurement.run_once()
