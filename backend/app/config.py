"""Runtime configuration, loaded from environment (.env). See root .env.example.

`.env` is looked up in the current directory *and* the repo root (`../.env`), so
`backend/` and `worker-colmap/` share one file when run from their own folders."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- Supabase ---
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "scans"

    # --- API ---
    max_upload_mb: int = 512
    #: per-object cap of the storage backend (Supabase: 50 MB on the current plan).
    #: Videos over this are re-encoded to fit (app/video.py); meshes are gzip'd.
    storage_object_max_mb: int = 48
    #: run the keyframe stage worker inside the API process (thread). Off by default:
    #: the reconstruction worker claims `pending` jobs and extracts keyframes itself
    #: from the video (one download instead of ~90 per-frame round trips). Turn on
    #: only for a worker that cannot run ffmpeg.
    run_keyframe_worker: bool = False

    # --- Keyframe extraction (ported VideoFrameExporter defaults) ---
    keyframe_interval_seconds: float = 0.35
    keyframe_max_frames: int = 350
    #: spread ~this many frames over the clip (0 = fixed interval). ~40 is the
    #: accuracy/speed sweet spot from the caliper sweep (D19).
    keyframe_target_frames: int = 40

    #: keyframes kept in storage after a run (thumbnails / fixtures). The full set is
    #: archived after measurement, off the critical path.
    archive_keyframes: bool = True

    # --- Measurement (carried onto every job's config → reproducible) ---
    grace_ring_mm: float = 3.0  # FR-07, configurable, never hard-coded
    tolerance_mm: float = 1.0  # FR-09
    marker_side_mm: float = 50.0  # printed ArUco card edge length
    aruco_dict: str = "LEGACY_4X4_50"  # cards printed by the legacy Mac app (bit-inverted)
    gcode_dialect: str = "grbl"  # 'grbl' (P4 sim target) | 'stoma-plotter' (legacy)

    # --- Queue robustness ---
    #: a claim whose heartbeat is older than this is considered dead and requeued.
    #: Workers heartbeat claimed_at every 60 s while a stage runs, so this is about
    #: dead workers, not slow stages.
    claim_timeout_s: float = 600.0
    #: a job is failed for good after this many claims of the same stage
    max_attempts: int = 2
    #: hard timeout for one reconstruction run (safety bound, not the FR-11 target)
    reconstruct_timeout_s: float = 1800.0
    #: hard timeout for the measurement stage
    measure_timeout_s: float = 300.0

    # --- Web app (P3) ---
    # Comma-separated allowed CORS origins; "*" for the demo phase.
    cors_origins: str = "*"

    @property
    def cors_allow_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def storage_object_max_bytes(self) -> int:
        return self.storage_object_max_mb * 1024 * 1024

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    def measure_config(self) -> dict:
        """The measurement knobs stamped onto a new job's `config` so the run is
        reproducible and every stage reads one source of truth (the job)."""
        return {
            "grace_ring_mm": self.grace_ring_mm,
            "tolerance_mm": self.tolerance_mm,
            "marker_side_mm": self.marker_side_mm,
            "aruco_dict": self.aruco_dict,
            "gcode_dialect": self.gcode_dialect,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
