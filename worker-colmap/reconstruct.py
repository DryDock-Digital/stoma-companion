"""COLMAP + OpenMVS engine — a `Reconstructor` (backend/app/queue.py) that shells
out to pipeline.sh. The queue and everything downstream stay engine-agnostic; all
COLMAP/OpenMVS specifics are confined to this file and pipeline.sh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ColmapReconstructor:
    """images in → OBJ mesh out, via the COLMAP+OpenMVS pipeline script."""

    name = "colmap+openmvs"

    def __init__(self, script: Path | None = None) -> None:
        self.script = script or Path(__file__).with_name("pipeline.sh")

    def reconstruct(self, keyframe_dir: Path, work_dir: Path) -> Path:
        if shutil.which("colmap") is None:
            raise RuntimeError(
                "colmap not found on PATH — run inside the worker-colmap image "
                "or on a droplet with COLMAP+OpenMVS installed."
            )
        output_obj = work_dir / "mesh.obj"
        subprocess.run(
            ["bash", str(self.script), str(keyframe_dir), str(work_dir), str(output_obj)],
            check=True,
        )
        if not output_obj.exists():
            raise RuntimeError("pipeline.sh completed but produced no mesh.obj")
        return output_obj
