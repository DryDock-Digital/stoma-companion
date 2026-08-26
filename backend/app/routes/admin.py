"""Admin test bench (engineer-facing, not the patient flow).

Upload a test video with its model name / caliper truth, watch the run, read every
number the pipeline produced (timings per stage vs the 120 s target, diagnostics,
Ø-vs-height profile, outlines, artefact links) and enter or correct the caliper
truth afterwards — deviation and pass/fail are recomputed and the verification log
row (P5-1) kept in sync. No auth in this phase (NFR-07 deferral); the web app only
reaches this under /admin and the patient flow never links to it.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import paths
from ..config import Settings
from ..cycle_time import CycleReport
from ..models import Job, JobStatus, ScanCreated
from ..runlog import RunRecord, RunStore
from ..store import JobStore
from .scans import _ACCEPTED_PREFIXES, _ACCEPTED_TYPES, get_app_settings, get_store

router = APIRouter(prefix="/admin", tags=["admin"])

#: config keys the engineer may set/correct on a job
_TRUTH_KEYS = ("model_name", "truth_mm", "reference_point", "notes")
_DEFAULT_REFERENCE_POINT = "base at skin junction"


def get_run_store(request: Request) -> RunStore:
    return request.app.state.run_store


# --- response shapes -------------------------------------------------------


class AdminScanSummary(BaseModel):
    id: str
    created_at: Any = None
    updated_at: Any = None
    status: JobStatus
    model_name: str | None = None
    truth_mm: float | None = None
    reference_point: str | None = None
    diameter_mm: float | None = None
    deviation_mm: float | None = None
    within_tolerance: bool | None = None
    tolerance_mm: float = 1.0
    total_s: float | None = None
    engine: str | None = None
    error: str | None = None
    attempts: int = 0


class AdminScanDetail(AdminScanSummary):
    config: dict[str, Any]
    result: dict[str, Any] | None = None
    error_detail: str | None = None
    error_stage: str | None = None
    worker_id: str | None = None
    claimed_at: Any = None
    keyframe_count: int | None = None
    notes: str | None = None
    timings: dict[str, Any]
    artifacts: dict[str, Any]
    run: dict[str, Any] | None = None


class TruthPatch(BaseModel):
    model_name: str | None = None
    truth_mm: float | None = None
    reference_point: str | None = None
    notes: str | None = None


# --- helpers ---------------------------------------------------------------


def _deviation(result: dict[str, Any] | None, truth: float | None, tol: float):
    if not result or result.get("diameter_mm") is None:
        return None, None
    if truth is None:
        return None, None
    dev = float(result["diameter_mm"]) - float(truth)
    return round(dev, 3), abs(dev) <= tol


def _summary(job: Job) -> AdminScanSummary:
    cfg = job.config or {}
    res = job.result or {}
    tol = float(cfg.get("tolerance_mm", res.get("tolerance_mm", 1.0)))
    truth = cfg.get("truth_mm")
    truth = None if truth in (None, "") else float(truth)
    dev, within = _deviation(res, truth, tol)
    timings = res.get("timings_s") or {}
    return AdminScanSummary(
        id=job.id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        status=job.status,
        model_name=cfg.get("model_name"),
        truth_mm=truth,
        reference_point=cfg.get("reference_point"),
        diameter_mm=res.get("diameter_mm"),
        deviation_mm=dev,
        within_tolerance=within,
        tolerance_mm=tol,
        total_s=round(sum(timings.values()), 2) if timings else None,
        engine=job.engine or res.get("engine"),
        error=job.error,
        attempts=job.attempts,
    )


def _safe_url(store: JobStore, key: str | None) -> str | None:
    if not key:
        return None
    try:
        return store.signed_url(key, 3600)
    except Exception:  # noqa: BLE001 — a missing artefact is not an error here
        return None


def _detail(job: Job, store: JobStore, run_store: RunStore) -> AdminScanDetail:
    summary = _summary(job)
    res = dict(job.result or {})
    res.pop("gcode", None)
    report = CycleReport.from_job_result(job.result)
    keyframes: list[str] = []
    if job.keyframes_prefix:
        try:
            keys = [
                k
                for k in store.list_objects(job.keyframes_prefix)
                if k.rsplit("/", 1)[-1].startswith("frame_")
            ]
            step = max(1, len(keys) // 6)
            keyframes = [u for u in (_safe_url(store, k) for k in keys[::step][:6]) if u]
        except Exception:  # noqa: BLE001
            keyframes = []
    run = run_store.find_by_job(job.id)
    return AdminScanDetail(
        **summary.model_dump(),
        config=job.config or {},
        result=res or None,
        error_detail=job.error_detail,
        error_stage=job.error_stage,
        worker_id=job.worker_id,
        claimed_at=job.claimed_at,
        keyframe_count=job.keyframe_count,
        notes=(job.config or {}).get("notes"),
        timings={
            "stages": report.timings,
            "total_s": round(report.total, 2),
            "target_s": report.target_s,
            "within_budget": report.within_budget,
            "bottleneck": report.bottleneck,
        },
        artifacts={
            "video_url": _safe_url(store, job.video_path),
            "mesh_url": _safe_url(store, job.mesh_path),
            "poses_url": _safe_url(store, job.poses_path),
            "gcode_url": _safe_url(store, job.gcode_path),
            "keyframe_urls": keyframes,
        },
        run=run.model_dump(mode="json") if run else None,
    )


def _sync_run_log(job: Job, run_store: RunStore) -> None:
    """Keep the P5-1 verification row consistent with the job's truth + result."""
    res = job.result or {}
    if res.get("diameter_mm") is None:
        return
    cfg = job.config or {}
    truth = cfg.get("truth_mm")
    truth = None if truth in (None, "") else float(truth)
    existing = run_store.find_by_job(job.id)
    fields = dict(
        model_name=cfg.get("model_name") or job.id,
        measured_mm=float(res["diameter_mm"]),
        truth_mm=truth,
        tolerance_mm=float(cfg.get("tolerance_mm", res.get("tolerance_mm", 1.0))),
        job_id=job.id,
        video_ref=job.video_path,
        engine=job.engine or res.get("engine"),
        method=f"auto-height/{res.get('orientation_method', '')}",
        reference_point=cfg.get("reference_point"),
        config={k: v for k, v in cfg.items() if k != "notes"},
        notes=cfg.get("notes"),
    )
    rebuilt = RunRecord.build(**fields)
    if existing is None:
        run_store.insert(rebuilt)
    else:
        run_store.update(
            rebuilt.model_copy(update={"id": existing.id, "created_at": existing.created_at})
        )


# --- endpoints -------------------------------------------------------------


@router.post("/scans", status_code=201, response_model=ScanCreated)
async def admin_create_scan(
    video: UploadFile = File(...),
    model_name: str | None = Form(None),
    truth_mm: float | None = Form(None),
    reference_point: str | None = Form(None),
    notes: str | None = Form(None),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> ScanCreated:
    content_type = video.content_type or ""
    if not (content_type.startswith(_ACCEPTED_PREFIXES) or content_type in _ACCEPTED_TYPES):
        raise HTTPException(415, f"Unsupported upload type: {content_type!r}; expected a video.")
    t0 = time.perf_counter()
    data = await video.read()
    if not data:
        raise HTTPException(400, "Empty upload.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_mb} MB limit.")

    config = {
        "keyframe_interval_seconds": settings.keyframe_interval_seconds,
        "keyframe_max_frames": settings.keyframe_max_frames,
        **settings.measure_config(),
        "model_name": (model_name or "").strip() or None,
        "truth_mm": truth_mm,
        "reference_point": (reference_point or "").strip() or _DEFAULT_REFERENCE_POINT,
        "notes": (notes or "").strip() or None,
        "source": "admin",
        "source_filename": video.filename,
    }
    job = store.create_job(config=config)
    video_key = paths.video_key(job.id)
    store.put_object(video_key, data, content_type or "video/quicktime")
    store.update_job(job.id, video_path=video_key)
    store.patch_result(
        job.id,
        {"timings_s": {"upload": round(time.perf_counter() - t0, 4)}, "upload_bytes": len(data)},
    )
    return ScanCreated(id=job.id, status=JobStatus.PENDING)


@router.get("/scans")
async def admin_list_scans(limit: int = 50, store: JobStore = Depends(get_store)) -> dict:
    jobs = store.list_jobs(limit=max(1, min(limit, 500)))
    return {"jobs": [_summary(j).model_dump(mode="json") for j in jobs]}


@router.get("/scans/{job_id}", response_model=AdminScanDetail)
async def admin_get_scan(
    job_id: str,
    store: JobStore = Depends(get_store),
    run_store: RunStore = Depends(get_run_store),
) -> AdminScanDetail:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan not found.")
    return _detail(job, store, run_store)


@router.patch("/scans/{job_id}", response_model=AdminScanDetail)
async def admin_patch_scan(
    job_id: str,
    patch: TruthPatch,
    store: JobStore = Depends(get_store),
    run_store: RunStore = Depends(get_run_store),
) -> AdminScanDetail:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan not found.")
    cfg = dict(job.config or {})
    provided = patch.model_dump(exclude_unset=True)
    for key in _TRUTH_KEYS:
        if key in provided:
            cfg[key] = provided[key]
    job = store.update_job(job.id, config=cfg)

    # recompute deviation/pass on the stored result so the patient API agrees too
    res = dict(job.result or {})
    if res.get("diameter_mm") is not None:
        truth = cfg.get("truth_mm")
        tol = float(cfg.get("tolerance_mm", res.get("tolerance_mm", 1.0)))
        dev, within = _deviation(res, None if truth in (None, "") else float(truth), tol)
        job = store.patch_result(job.id, {"deviation_mm": dev, "within_tolerance": within})
    _sync_run_log(job, run_store)
    return _detail(job, store, run_store)


@router.get("/scans/{job_id}/gcode", response_class=PlainTextResponse)
async def admin_get_gcode(job_id: str, store: JobStore = Depends(get_store)) -> str:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Scan not found.")
    if not job.gcode_path:
        raise HTTPException(404, "No G-code for this scan yet.")
    return store.get_object(job.gcode_path).decode()


@router.get("/report.csv", response_class=PlainTextResponse)
async def admin_report_csv(run_store: RunStore = Depends(get_run_store)) -> PlainTextResponse:
    from ..verify.report import to_csv  # lazy: the verify package needs the measure extra

    runs = run_store.list()
    return PlainTextResponse(
        to_csv(runs),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="verification-runs.csv"'},
    )
