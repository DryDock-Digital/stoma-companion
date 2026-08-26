"""Standalone keyframe-stage worker: `python -m app.keyframe_worker`.

Use this instead of the in-API thread when the API and the extraction should
scale or restart independently (set RUN_KEYFRAME_WORKER=0 on the API)."""

from __future__ import annotations

import logging
import os

from .config import Settings
from .keyframes import KeyframeParams
from .pipeline import KeyframeWorker
from .store import build_store


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    worker = KeyframeWorker(
        build_store(settings),
        KeyframeParams(
            interval_seconds=settings.keyframe_interval_seconds,
            max_frames=settings.keyframe_max_frames,
        ),
        worker_id=os.environ.get("WORKER_ID", "keyframes-1"),
        claim_timeout_s=settings.claim_timeout_s,
        max_attempts=settings.max_attempts,
    )
    worker.run_forever(poll_interval=float(os.environ.get("WORKER_POLL_INTERVAL", "2")))


if __name__ == "__main__":
    main()
