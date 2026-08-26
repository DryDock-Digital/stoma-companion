"""Canonical object keys inside the `scans` bucket. Single source of truth so the
API, keyframe stage and reconstruction workers agree on layout.

    <job_id>/input.mov
    <job_id>/keyframes/frame_00000.jpg …
    <job_id>/mesh.obj.gz       # gzip'd OBJ: Supabase caps objects at 50 MB, raw OBJs run 100 MB+
    <job_id>/poses.json        # engine-neutral camera poses (measure/poses.py)
    <job_id>/wafer.gcode       # the cut program (P4 sender reads this, never `result`)
"""

from __future__ import annotations


def video_key(job_id: str) -> str:
    return f"{job_id}/input.mov"


def keyframes_prefix(job_id: str) -> str:
    return f"{job_id}/keyframes/"


def keyframe_key(job_id: str, index: int) -> str:
    return f"{keyframes_prefix(job_id)}frame_{index:05d}.jpg"


def calibration_key(job_id: str) -> str:
    return f"{keyframes_prefix(job_id)}calibration_top.jpg"


def mesh_key(job_id: str) -> str:
    return f"{job_id}/mesh.obj.gz"


def poses_key(job_id: str) -> str:
    return f"{job_id}/poses.json"


def gcode_key(job_id: str) -> str:
    return f"{job_id}/wafer.gcode"
