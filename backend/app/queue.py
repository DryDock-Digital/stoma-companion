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

import gzip
import logging
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        claim_timeout_s: float = 600.0,
        max_attempts: int = 2,
        watchdog_every_s: float = 60.0,
        heartbeat_s: float = 60.0,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.claim_timeout_s = claim_timeout_s
        self.max_attempts = max_attempts
        self.watchdog_every_s = watchdog_every_s
        self.heartbeat_s = heartbeat_s
        self._last_watchdog = 0.0

    def run_once(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    @contextmanager
    def heartbeat(self, job_id: str):
        """Touch `claimed_at` every `heartbeat_s` while a stage runs, so the stale-claim
        watchdog only ever fires on a worker that is actually dead — never on a slow
        (CPU) reconstruction. Failures to heartbeat are logged, not fatal."""
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(self.heartbeat_s):
                try:
                    self.store.update_job(job_id, claimed_at=datetime.now(UTC))
                except Exception:  # noqa: BLE001
                    log.warning("heartbeat failed for job %s", job_id, exc_info=True)

        t = threading.Thread(target=beat, name=f"heartbeat-{job_id[:8]}", daemon=True)
        t.start()
        try:
            yield
        finally:
            stop.set()
            t.join(timeout=5)

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
        *,
        extract_keyframes: bool = True,
        archive_keyframes: bool = True,
        **poller_kwargs,
    ) -> None:
        super().__init__(store, worker_id, **poller_kwargs)
        self.reconstructor = reconstructor
        self.measurer = measurer
        #: claim `pending` jobs and extract keyframes from the video locally (one
        #: download instead of ~90 per-frame round trips each way)
        self.extract_keyframes = extract_keyframes
        #: after measurement, push the full keyframe set to storage (thumbnails,
        #: fixtures, the standalone MeasurementWorker) — off the critical path
        self.archive_keyframes = archive_keyframes

    def claim(self) -> Job | None:
        """Atomically take the next job: `keyframes_ready` first (already
        extracted), else a `pending` one whose video is stored (we extract).
        Returns None when the queue is empty."""
        job = self.store.claim_next_job(
            self.worker_id,
            self.reconstructor.name,
            JobStatus.KEYFRAMES_READY,
            JobStatus.RECONSTRUCTING,
        )
        if job is None and self.extract_keyframes:
            job = self.store.claim_next_job(
                self.worker_id, self.reconstructor.name, JobStatus.PENDING, JobStatus.EXTRACTING
            )
        return job

    def _extract_locally(self, job: Job, keyframe_dir: Path, tmp_dir: Path, timer) -> Job:
        """pending → extracting → (frames on local disk) → reconstructing."""
        from .keyframes import KeyframeParams, extract_keyframes

        cfg = job.config or {}
        params = KeyframeParams(
            interval_seconds=float(cfg.get("keyframe_interval_seconds", 0.35)),
            max_frames=int(cfg.get("keyframe_max_frames", 350)),
        )
        video = tmp_dir / "input.bin"
        with timer.stage("download"):
            video.write_bytes(self.store.get_object(job.video_path or paths.video_key(job.id)))
        with timer.stage("extract"):
            result = extract_keyframes(video, keyframe_dir, params)
        return self.store.update_job(
            job.id,
            status=JobStatus.RECONSTRUCTING,
            keyframe_count=result.count,
            keyframes_prefix=paths.keyframes_prefix(job.id),
        )

    def _archive_keyframes(self, job_id: str, keyframe_dir: Path, timer) -> None:
        with timer.stage("archive"):
            for f in sorted(keyframe_dir.glob("*.jpg")):
                self.store.put_object(
                    f"{paths.keyframes_prefix(job_id)}{f.name}", f.read_bytes(), "image/jpeg"
                )

    def process(self, job: Job) -> None:
        """Download keyframes, reconstruct, upload mesh + poses, mark mesh_ready;
        then measure inline when a measurer is attached."""
        stage = "extract" if job.status == JobStatus.EXTRACTING else "reconstruct"
        try:
            with tempfile.TemporaryDirectory() as tmp, self.heartbeat(job.id):
                tmp_dir = Path(tmp)
                keyframe_dir = tmp_dir / "keyframes"
                keyframe_dir.mkdir()
                work_dir = tmp_dir / "work"
                work_dir.mkdir()
                timer = StageTimer()

                if job.status == JobStatus.EXTRACTING:
                    job = self._extract_locally(job, keyframe_dir, tmp_dir, timer)
                    stage = "reconstruct"
                else:
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
                    # gzip: ASCII OBJ compresses ~4x; Supabase rejects objects > 50 MB
                    mesh_gz = gzip.compress(Path(out.mesh_path).read_bytes(), compresslevel=6)
                    self.store.put_object(mesh_key, mesh_gz, "application/gzip")
                    self.store.put_object(
                        poses_key, poses_mod.dumps(out.cameras).encode(), "application/json"
                    )
                self.store.update_job(
                    job.id,
                    status=JobStatus.MESH_READY,
                    mesh_path=mesh_key,
                    poses_path=poses_key,
                    attempts=0,  # attempts count per stage, not per job
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
                # the job is already `measured` for the client; archive frames after
                if self.archive_keyframes and not self.store.list_objects(
                    paths.keyframes_prefix(job.id)
                ):
                    try:
                        self._archive_keyframes(job.id, keyframe_dir, timer)
                        self.store.patch_result(
                            job.id, {"timings_s": {"archive": timer.get("archive")}}
                        )
                    except Exception:  # noqa: BLE001 — never fail a measured job
                        log.exception("keyframe archive failed for job %s", job.id)
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
    store.update_job(job.id, attempts=0, **update)
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
            with tempfile.TemporaryDirectory() as tmp, self.heartbeat(job.id):
                tmp_dir = Path(tmp)
                keyframe_dir = tmp_dir / "keyframes"
                keyframe_dir.mkdir()
                timer = StageTimer()
                with timer.stage("download"):
                    _download_keyframes(self.store, job, keyframe_dir)
                    mesh_path = tmp_dir / "mesh.obj"
                    raw = self.store.get_object(job.mesh_path or paths.mesh_key(job.id))
                    mesh_path.write_bytes(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
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
