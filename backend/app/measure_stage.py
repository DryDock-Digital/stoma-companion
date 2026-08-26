"""The measurement stage (P1-10): mesh + poses + keyframes → job result.

Engine-agnostic: cameras arrive as `PinholeCamera`s from `poses.json`, the mesh as
an OBJ. Parameters come from the job's `config` (stamped at upload from
`Settings.measure_config()`, overridable per job — FR-07 ring, marker size,
dialect, …). Also writes the verification run log row (P5-1) and enforces a hard
wall-clock timeout so a pathological mesh can't pin the worker.
"""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path

from .errors import StageTimeout
from .measure.measure_scan import MeasureParams, measure_scan
from .measure.orientation import PinholeCamera
from .models import Job
from .runlog import RunRecord, RunStore

log = logging.getLogger(__name__)


class MeasureStage:
    def __init__(self, run_store: RunStore | None = None, *, timeout_s: float = 300.0) -> None:
        self.run_store = run_store
        self.timeout_s = timeout_s

    def measure(
        self, job: Job, mesh_path: Path, cameras: dict[str, PinholeCamera], keyframe_dir: Path
    ) -> dict:
        import cv2
        import numpy as np
        import trimesh

        cfg = job.config or {}
        params = MeasureParams.from_config(cfg)

        keyframes: dict[str, np.ndarray] = {}
        for name in cameras:
            p = keyframe_dir / name
            if p.exists():
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    keyframes[name] = img

        # process=True merges the duplicated vertex records OBJ exporters emit for
        # texture seams (3.9M records → 657k vertices on the first real mesh)
        mesh = trimesh.load(str(mesh_path), force="mesh", process=True)
        vertices = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(mesh.faces, dtype=int)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                measure_scan, vertices, faces, cameras, keyframes, params=params, engine=job.engine
            )
            try:
                res = fut.result(timeout=self.timeout_s)
            except concurrent.futures.TimeoutError as exc:
                raise StageTimeout(
                    f"measurement exceeded {self.timeout_s:g}s", stage="measure"
                ) from exc

        if self.run_store is not None:
            try:
                self.run_store.insert(
                    RunRecord.build(
                        model_name=cfg.get("model_name") or job.id,
                        measured_mm=res.diameter_mm,
                        truth_mm=res.truth_mm,
                        tolerance_mm=res.tolerance_mm,
                        job_id=job.id,
                        video_ref=job.video_path,
                        engine=job.engine,
                        method=f"auto-height/{res.orientation_method}",
                        reference_point=cfg.get("reference_point"),
                        config={**params.to_config(), "engine": job.engine},
                    )
                )
            except Exception:  # noqa: BLE001 — the run log must never fail a scan
                log.exception("run log insert failed for job %s", job.id)

        result = res.result_json()
        result["gcode"] = res.gcode  # popped + stored as an object by queue.measure_job
        return result
