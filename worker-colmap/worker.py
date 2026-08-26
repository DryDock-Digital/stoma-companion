"""COLMAP reconstruction worker entrypoint (P1-4).

Two modes:

  # queue mode (default): poll Supabase, reconstruct claimed jobs
  python worker.py

  # local mode: reconstruct a keyframe directory straight to an OBJ — the P1-4
  # acceptance path ("produces a mesh from fixture keyframes"), no queue needed
  python worker.py --local <keyframe_dir> <output.obj>

The queue contract + store live in the backend `app` package, imported here so
there is exactly one implementation of the contract (install with
`pip install -e ../backend`; the Docker image does this).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from app.config import Settings
from app.queue import ReconstructionWorker
from app.store import build_store

from reconstruct import ColmapReconstructor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker-colmap")


def run_local(keyframe_dir: Path, output_obj: Path) -> None:
    engine = ColmapReconstructor()
    with tempfile.TemporaryDirectory() as tmp:
        mesh = engine.reconstruct(keyframe_dir, Path(tmp))
        output_obj.parent.mkdir(parents=True, exist_ok=True)
        output_obj.write_bytes(Path(mesh).read_bytes())
    log.info("wrote mesh → %s", output_obj)


def run_queue() -> None:
    settings = Settings()
    if not settings.supabase_configured:
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — see .env.example")
    store = build_store(settings)
    worker = ReconstructionWorker(
        store, ColmapReconstructor(), worker_id=os.environ.get("WORKER_ID", "colmap-1")
    )
    poll = float(os.environ.get("WORKER_POLL_INTERVAL", "5"))
    worker.run_forever(poll_interval=poll)


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
