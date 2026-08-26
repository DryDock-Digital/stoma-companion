"""Parse a COLMAP sparse model (text export) into engine-neutral PinholeCameras.

This is the COLMAP side of the reconstruction contract: `pipeline.sh` exports
`cameras.txt` + `images.txt` (`colmap model_converter --output_type TXT`) and
`ColmapReconstructor` turns them into the `{image_name: PinholeCamera}` map that
goes into `poses.json`. Nothing in `backend/` knows COLMAP's file formats.

COLMAP conventions: `images.txt` stores the world→camera rotation as a quaternion
(qw, qx, qy, qz) and translation (tx, ty, tz) — exactly PinholeCamera's R, t. The
OpenMVS dense mesh shares COLMAP's world frame, so the recovered scale/normal apply
directly to the mesh. **Lens distortion is carried through** (SIMPLE_RADIAL is
COLMAP's default; a phone wide lens puts several px of error at the marker
corners) and removed from observations before triangulation.
"""

from __future__ import annotations

import numpy as np

from app.measure.orientation import PinholeCamera


def _intrinsics(
    model: str, w: int, h: int, params: list[float]
) -> tuple[np.ndarray, np.ndarray | None]:
    """(K, OpenCV dist[k1,k2,p1,p2,k3]) from a COLMAP camera model."""
    dist: np.ndarray | None = None
    if model == "PINHOLE":
        fx, fy, cx, cy = params[:4]
    elif model == "OPENCV":
        fx, fy, cx, cy, k1, k2, p1, p2 = params[:8]
        dist = np.array([k1, k2, p1, p2, 0.0])
    elif model == "FULL_OPENCV":
        fx, fy, cx, cy, k1, k2, p1, p2, k3 = params[:9]
        dist = np.array([k1, k2, p1, p2, k3])
    elif model == "SIMPLE_PINHOLE":
        f, cx, cy = params[:3]
        fx = fy = f
    elif model == "SIMPLE_RADIAL":
        f, cx, cy, k = params[:4]
        fx = fy = f
        dist = np.array([k, 0.0, 0.0, 0.0, 0.0])
    elif model == "RADIAL":
        f, cx, cy, k1, k2 = params[:5]
        fx = fy = f
        dist = np.array([k1, k2, 0.0, 0.0, 0.0])
    else:  # fisheye / unknown — best effort, no distortion model
        fx = fy = params[0]
        cx, cy = w / 2, h / 2
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
    if dist is not None and not np.any(dist):
        dist = None
    return K, dist


def _quat_to_rot(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
    """COLMAP world→camera unit quaternion → rotation matrix."""
    n = (qw * qw + qx * qx + qy * qy + qz * qz) ** 0.5 or 1.0
    w, x, y, z = qw / n, qx / n, qy / n, qz / n
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def parse_cameras(text: str) -> dict[int, np.ndarray]:
    """cameras.txt → {camera_id: (model, width, height, params)} kept as a tuple."""
    cams: dict[int, tuple] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        cam_id = int(f[0])
        model = f[1]
        w, h = int(f[2]), int(f[3])
        params = [float(x) for x in f[4:]]
        cams[cam_id] = (model, w, h, params)
    return cams


def parse_model(cameras_txt: str, images_txt: str) -> dict[str, PinholeCamera]:
    """cameras.txt + images.txt → {image_name: PinholeCamera}."""
    cams = parse_cameras(cameras_txt)
    out: dict[str, PinholeCamera] = {}
    lines = [ln for ln in images_txt.splitlines() if ln.strip() and not ln.startswith("#")]
    # images.txt: each image is two lines; the pose is on the first, points on the second.
    for i in range(0, len(lines), 2):
        f = lines[i].split()
        if len(f) < 10:
            continue
        qw, qx, qy, qz = (float(v) for v in f[1:5])
        tx, ty, tz = (float(v) for v in f[5:8])
        cam_id = int(f[8])
        name = f[9]
        model, w, h, params = cams[cam_id]
        K, dist = _intrinsics(model, w, h, params)
        out[name] = PinholeCamera(
            K=K,
            R=_quat_to_rot(qw, qx, qy, qz),
            t=np.array([tx, ty, tz]),
            dist=dist,
            image_size=(w, h),
            meta={"colmap_model": model},
        )
    return out


def load_sparse_txt(sparse_dir) -> dict[str, PinholeCamera]:
    """`{name: PinholeCamera}` from a directory holding cameras.txt + images.txt."""
    from pathlib import Path

    d = Path(sparse_dir)
    return parse_model((d / "cameras.txt").read_text(), (d / "images.txt").read_text())
