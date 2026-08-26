"""COLMAP + OpenMVS engine — a `Reconstructor` (backend/app/queue.py) that shells
out to pipeline.sh and converts COLMAP's pose export to the engine-neutral
`ReconstructionOutput`. The queue and everything downstream stay engine-agnostic;
all COLMAP/OpenMVS specifics are confined to this file, colmap_model.py and
pipeline.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.errors import StageError, StageTimeout
from app.queue import ReconstructionOutput
from colmap_model import load_sparse_txt


class ReconstructError(StageError):
    stage = "reconstruct"


class ColmapReconstructor:
    """images in → mesh + poses out, via the COLMAP+OpenMVS pipeline script."""

    name = "colmap+openmvs"

    def __init__(self, script: Path | None = None, timeout_s: float | None = None) -> None:
        self.script = script or Path(__file__).with_name("pipeline.sh")
        self.timeout_s = (
            timeout_s
            if timeout_s is not None
            else float(os.environ.get("RECONSTRUCT_TIMEOUT_S", "1800"))
        )

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> ReconstructionOutput:
        if shutil.which("colmap") is None:
            raise ReconstructError(
                "colmap not found on PATH — run inside the worker-colmap image "
                "or on a droplet with COLMAP+OpenMVS installed."
            )
        output_obj = work_dir / "mesh.obj"
        try:
            proc = subprocess.run(
                ["bash", str(self.script), str(keyframe_dir), str(work_dir), str(output_obj)],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise StageTimeout(
                f"pipeline.sh exceeded {self.timeout_s:g}s", stage="reconstruct"
            ) from exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise ReconstructError(f"pipeline.sh exited {proc.returncode}: {tail}")
        if not output_obj.exists():
            raise ReconstructError("pipeline.sh completed but produced no mesh.obj")

        sparse_txt = work_dir / "sparse_txt"
        cameras = load_sparse_txt(sparse_txt) if sparse_txt.exists() else {}
        if not cameras:
            raise ReconstructError("COLMAP registered no images (no poses exported)")
        n_images = len(list(keyframe_dir.glob("frame_*.jpg")))
        settings_used = {
            k: os.environ.get(k, v)
            for k, v in (
                ("COLMAP_USE_GPU", "1"),
                ("DENSE_ENGINE", "openmvs"),
                ("PMS_ITERATIONS", "5"),
                ("PMS_WINDOW_RADIUS", "5"),
                ("POISSON_DEPTH", "10"),
                ("POISSON_TRIM", "7"),
                ("COLMAP_MAX_IMAGE_SIZE", "1600"),
                ("COLMAP_MAX_FEATURES", "4096"),
                ("COLMAP_SEQ_OVERLAP", "10"),
                ("MVS_RESOLUTION_LEVEL", "2"),
                ("MVS_NUMBER_VIEWS", "4"),
                ("MVS_MAX_RESOLUTION", "1200"),
                ("MESH_DECIMATE", "0.3"),
            )
        }
        gpu_name = None
        gpu_log = work_dir / "gpu.txt"
        if gpu_log.exists():
            gpu_name = gpu_log.read_text().strip() or None
        return ReconstructionOutput(
            mesh_path=output_obj,
            cameras=cameras,
            diagnostics={
                "engine": self.name,
                "input_frames": n_images,
                "registered_frames": len(cameras),
                "gpu": settings_used["COLMAP_USE_GPU"] == "1",
                "gpu_name": gpu_name,
                "settings": settings_used,
            },
        )
