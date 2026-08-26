"""Runtime configuration, loaded from environment (.env). See root .env.example."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Supabase ---
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "scans"

    # --- API ---
    max_upload_mb: int = 512

    # --- Keyframe extraction (ported VideoFrameExporter defaults) ---
    keyframe_interval_seconds: float = 0.35
    keyframe_max_frames: int = 350

    # --- Measurement ---
    grace_ring_mm: float = 3.0  # FR-07, configurable, never hard-coded

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
