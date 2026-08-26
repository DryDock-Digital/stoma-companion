"""Measurement hook for the reconstruction worker (P1-10).

Runs inline after reconstruction while the mesh, keyframes, and COLMAP pose export
are still on local disk. Parses the poses, detects the ArUco marker in the keyframes
to recover real-world scale + orientation, measures the base, and returns the
`result` dict the web app renders — also logging a verification run (P5).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import trimesh

from app.measure.colmap_model import parse_model
from app.measure.measure_scan import measure_scan
from app.runlog import RunRecord, RunStore


def make_measure_hook(run_store: RunStore, *, default_marker_side_mm: float = 50.0):
    def hook(job, mesh_path: Path, keyframe_dir: Path, work_dir: Path) -> dict | None:
        cfg = job.config or {}

        # COLMAP poses exported next to the mesh (pipeline.sh model_converter).
        sparse = Path(mesh_path).parent / "sparse_txt"
        cameras = parse_model(
            (sparse / "cameras.txt").read_text(),
            (sparse / "images.txt").read_text(),
        )

        keyframes: dict[str, np.ndarray] = {}
        for name in cameras:
            p = keyframe_dir / name
            if p.exists():
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    keyframes[name] = img

        mesh = trimesh.load(str(mesh_path), force="mesh", process=False)
        vertices = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(mesh.faces, dtype=int)

        res = measure_scan(
            vertices, faces, cameras, keyframes,
            marker_side_mm=float(cfg.get("marker_side_mm", default_marker_side_mm)),
            marker_id=cfg.get("marker_id"),
            grace_ring_mm=float(cfg.get("grace_ring_mm", 3.0)),
            tolerance_mm=float(cfg.get("tolerance_mm", 1.0)),
            truth_mm=cfg.get("truth_mm"),
            engine="colmap+openmvs",
        )

        run_store.insert(
            RunRecord.build(
                model_name=cfg.get("model_name") or job.id,
                measured_mm=res.diameter_mm,
                truth_mm=res.truth_mm,
                tolerance_mm=res.tolerance_mm,
                job_id=job.id,
                video_ref=job.video_path,
                engine="colmap+openmvs",
                method="auto-height",
                reference_point=cfg.get("reference_point"),
                config=cfg,
            )
        )

        result = res.result_json()
        result["gcode"] = res.gcode
        return result

    return hook
