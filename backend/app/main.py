"""FastAPI application factory (P1-1).

The store is attached to `app.state` so it can be swapped in tests (in-memory)
without patching imports. The keyframe stage runs as a queue worker thread inside
this process by default (see `pipeline.KeyframeWorker`); disable with
`RUN_KEYFRAME_WORKER=0` when running `python -m app.keyframe_worker` separately.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .keyframes import KeyframeParams
from .pipeline import KeyframeWorker
from .routes import scans
from .store import JobStore, build_store

log = logging.getLogger(__name__)

if not logging.getLogger().handlers:  # uvicorn configures its own loggers, not ours
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(
    store: JobStore | None = None,
    settings: Settings | None = None,
    *,
    keyframe_worker: KeyframeWorker | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = app.state.keyframe_worker
        thread = None
        if worker is not None and settings.run_keyframe_worker:
            thread = threading.Thread(
                target=worker.run_forever,
                kwargs={"poll_interval": 1.0},
                daemon=True,
                name="keyframe-worker",
            )
            thread.start()
            log.info("keyframe worker thread started")
        yield

    app = FastAPI(title="Stoma Companion API", version="0.2.0", lifespan=lifespan)

    app.state.settings = settings
    app.state.store = store or build_store(settings)
    app.state.keyframe_worker = (
        keyframe_worker
        if keyframe_worker is not None
        else KeyframeWorker(
            app.state.store,
            KeyframeParams(
                interval_seconds=settings.keyframe_interval_seconds,
                max_frames=settings.keyframe_max_frames,
            ),
            claim_timeout_s=settings.claim_timeout_s,
            max_attempts=settings.max_attempts,
        )
    )

    # The web/Capacitor patient app is a separate origin (P3) and talks only to this
    # API. Origins are configurable; default "*" is fine for the demo phase (no auth,
    # no PHI yet — NFR-07). Tighten to the app origin before anything real.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        """Liveness + the one number that reveals a stuck queue (oldest claim age)."""
        try:
            queue = app.state.store.queue_stats()
            store_ok = True
        except Exception as exc:  # noqa: BLE001
            queue = {"error": str(exc)[:200]}
            store_ok = False
        return {
            "status": "ok" if store_ok else "degraded",
            "supabase": "on" if settings.supabase_configured else "off",
            "keyframe_worker": "in-process" if settings.run_keyframe_worker else "external",
            "queue": queue,
        }

    app.include_router(scans.router)
    log.info("app ready (store=%s)", type(app.state.store).__name__)
    return app


app = create_app()
