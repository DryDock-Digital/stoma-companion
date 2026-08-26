"""COLMAP reconstruction + measurement worker entrypoint (P1-4, P1-10).

Two modes:

  # queue mode (default): poll Supabase; reconstruct claimed jobs, measure inline,
  # and also pick up any `mesh_ready` job left by another engine (Mac fallback)
  python worker.py

  # local mode: reconstruct a keyframe directory straight to an OBJ (+ poses.json)
  python worker.py --local <keyframe_dir> <output.obj>

The queue contract, store and measurement stage live in the backend `app`
package, imported here so there is exactly one implementation of each
(`pip install -e "../backend[measure]"`; the Docker image does this).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from app.config import Settings
from app.measure import poses as poses_mod
from app.measure_stage import MeasureStage
from app.queue import CombinedWorker, MeasurementWorker, ReconstructionWorker
from app.runlog import build_run_store
from app.store import build_store
from reconstruct import ColmapReconstructor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker-colmap")


def run_local(keyframe_dir: Path, output_obj: Path) -> None:
    engine = ColmapReconstructor()
    with tempfile.TemporaryDirectory() as tmp:
        out = engine.reconstruct(keyframe_dir, Path(tmp))
        output_obj.parent.mkdir(parents=True, exist_ok=True)
        output_obj.write_bytes(Path(out.mesh_path).read_bytes())
        output_obj.with_name("poses.json").write_text(poses_mod.dumps(out.cameras))
    log.info("wrote mesh → %s (+ poses.json, %d frames)", output_obj, len(out.cameras))


def run_queue() -> None:
    settings = Settings()
    if not settings.supabase_configured:
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — see .env.example")
    store = build_store(settings)
    worker_id = os.environ.get("WORKER_ID", "colmap-1")
    measurer = MeasureStage(build_run_store(settings), timeout_s=settings.measure_timeout_s)
    poller_kwargs = {
        "claim_timeout_s": settings.claim_timeout_s,
        "max_attempts": settings.max_attempts,
    }
    reconstruction = ReconstructionWorker(
        store,
        ColmapReconstructor(timeout_s=settings.reconstruct_timeout_s),
        worker_id=worker_id,
        measurer=measurer,
        **poller_kwargs,
    )
    measurement = MeasurementWorker(store, measurer, worker_id=worker_id, **poller_kwargs)
    poll = float(os.environ.get("WORKER_POLL_INTERVAL", "5"))
    CombinedWorker(reconstruction, measurement).run_forever(poll_interval=poll)


def main() -> None:
    parser = argparse.ArgumentParser(description="COLMAP+OpenMVS reconstruction worker")
    parser.add_argument(
        "--local",
        nargs=2,
        metavar=("KEYFRAME_DIR", "OUTPUT_OBJ"),
        help="reconstruct a local keyframe dir to an OBJ and exit",
    )
    args = parser.parse_args()

    if args.local:
        run_local(Path(args.local[0]), Path(args.local[1]))
    else:
        run_queue()


if __name__ == "__main__":
    main()
