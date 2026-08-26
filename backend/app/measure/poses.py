"""Engine-neutral camera poses — the second half of the reconstruction contract.

`docs/queue-contract.md`: a reconstruction engine returns **a mesh and the camera
poses of the keyframes it used**, in the mesh's own coordinate frame. The poses are
what let the measurement stage triangulate the ArUco marker for real-world scale +
"up" (P1-6, P2-2) without knowing which engine ran. COLMAP writes cameras.txt /
images.txt; Apple's PhotogrammetrySession exposes per-sample transforms; both are
converted *inside their worker* to this one JSON shape:

    {
      "format": "stoma-poses-v1",
      "units": "scene",                       # engine units; scale comes from the marker
      "cameras": {
        "frame_00012.jpg": {
          "K": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
          "R": [[...], [...], [...]],         # world→camera rotation (OpenCV convention)
          "t": [tx, ty, tz],                  # X_cam = R·X_world + t
          "dist": [k1, k2, p1, p2, k3] | null,
          "image_size": [w, h] | null
        }, ...
      }
    }

Stored next to the mesh as `<job_id>/poses.json` (`paths.poses_key`).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from .orientation import PinholeCamera

FORMAT = "stoma-poses-v1"


def camera_to_dict(cam: PinholeCamera) -> dict[str, Any]:
    return {
        "K": np.asarray(cam.K, float).tolist(),
        "R": np.asarray(cam.R, float).tolist(),
        "t": np.asarray(cam.t, float).reshape(3).tolist(),
        "dist": None if cam.dist is None else np.asarray(cam.dist, float).tolist(),
        "image_size": None if cam.image_size is None else list(cam.image_size),
    }


def camera_from_dict(d: dict[str, Any]) -> PinholeCamera:
    dist = d.get("dist")
    size = d.get("image_size")
    return PinholeCamera(
        K=np.asarray(d["K"], float).reshape(3, 3),
        R=np.asarray(d["R"], float).reshape(3, 3),
        t=np.asarray(d["t"], float).reshape(3),
        dist=None if dist is None else np.asarray(dist, float),
        image_size=None if size is None else (int(size[0]), int(size[1])),
    )


def dumps(cameras: dict[str, PinholeCamera], *, units: str = "scene") -> str:
    return json.dumps(
        {
            "format": FORMAT,
            "units": units,
            "cameras": {name: camera_to_dict(c) for name, c in cameras.items()},
        }
    )


def loads(text: str | bytes) -> dict[str, PinholeCamera]:
    data = json.loads(text)
    if data.get("format") != FORMAT:
        raise ValueError(f"unexpected poses format {data.get('format')!r}; want {FORMAT}")
    return {name: camera_from_dict(d) for name, d in data["cameras"].items()}
