"""FastAPI application factory (P1-1).

The store and the post-upload processor are attached to `app.state` so they can be
swapped in tests (in-memory store, stub processor) without patching imports.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import Settings, get_settings
from .pipeline import run_keyframe_stage
from .routes import scans
from .store import JobStore, build_store

log = logging.getLogger(__name__)


def create_app(store: JobStore | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Stoma Companion API", version="0.1.0")

    app.state.settings = settings
    app.state.store = store or build_store(settings)
    # Default processor is the real keyframe stage; tests override app.state.processor.
    app.state.processor = run_keyframe_stage

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "supabase": "on" if settings.supabase_configured else "off"}

    app.include_router(scans.router)
    log.info("app ready (store=%s)", type(app.state.store).__name__)
    return app


app = create_app()
