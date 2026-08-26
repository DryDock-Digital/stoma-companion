"""Parse a COLMAP sparse model (text export) into PinholeCameras (P1-10).

The reconstruction worker exports `cameras.txt` + `images.txt`
(`colmap model_converter --output_type TXT`). This turns them into a
`{image_name: PinholeCamera}` map so the same multi-view triangulation used for
orientation (P2-2) can recover the ArUco marker's 3-D corners in the mesh's own
coordinate frame — giving real-world **scale** (P1-6) and **up** (FR-04).

COLMAP conventions: `images.txt` stores the world→camera rotation as a quaternion
(qw, qx, qy, qz) and translation (tx, ty, tz) — exactly PinholeCamera's R, t. The
OpenMVS dense mesh shares COLMAP's world frame, so the recovered scale/normal apply
directly to the mesh. Lens distortion is ignored (marker corners are near the image
centre and the effect on scale is negligible for the demo).
"""

from __future__ import annotations

import numpy as np

from .orientation import PinholeCamera


def _intrinsics(model: str, w: int, h: int, params: list[float]) -> np.ndarray:
    """K from a COLMAP camera model. Handles the common pinhole-ish models; falls
    back to (focal from first param, principal point at image centre)."""
    if model in ("PINHOLE", "OPENCV", "FULL_OPENCV"):
        fx, fy, cx, cy = params[0], params[1], params[2], params[3]
    elif model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        f, cx, cy = params[0], params[1], params[2]
        fx = fy = f
    else:  # unknown model — best effort
        fx = fy = params[0]
        cx, cy = w / 2, h / 2
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])


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
        out[name] = PinholeCamera(
            K=_intrinsics(model, w, h, params),
            R=_quat_to_rot(qw, qx, qy, qz),
            t=np.array([tx, ty, tz]),
        )
    return out
