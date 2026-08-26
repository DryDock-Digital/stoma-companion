"""The API image installs no `measure` extra: importing the app must not need numpy
(the first live build after the review failed exactly here)."""

from __future__ import annotations

import subprocess
import sys


def test_app_imports_without_numpy():
    code = (
        "import sys; sys.modules['numpy']=None; sys.modules['cv2']=None; "
        "sys.modules['trimesh']=None; sys.modules['shapely']=None; "
        "import app.main, app.pipeline, app.queue, app.keyframe_worker; print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout
