"""The worker modules must import (and honour the contract) without COLMAP."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import StageError


def test_reconstructor_raises_clear_error_without_colmap(tmp_path, monkeypatch):
    import reconstruct

    monkeypatch.setattr(reconstruct.shutil, "which", lambda _: None)
    engine = reconstruct.ColmapReconstructor()
    assert engine.name == "colmap+openmvs"
    with pytest.raises(StageError) as ei:
        engine.reconstruct(tmp_path, tmp_path)
    assert "colmap not found" in str(ei.value)
    assert ei.value.stage == "reconstruct"


def test_reconstructor_times_out(tmp_path, monkeypatch):
    import reconstruct

    monkeypatch.setattr(reconstruct.shutil, "which", lambda _: "/usr/bin/colmap")
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/bash\nsleep 5\n")
    engine = reconstruct.ColmapReconstructor(script=slow, timeout_s=0.2)
    with pytest.raises(StageError) as ei:
        engine.reconstruct(tmp_path, tmp_path)
    assert ei.value.stage == "reconstruct"
    assert "longer than expected" in ei.value.user_message


def test_reconstructor_surfaces_script_failure(tmp_path, monkeypatch):
    import reconstruct

    monkeypatch.setattr(reconstruct.shutil, "which", lambda _: "/usr/bin/colmap")
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/bash\necho 'mapper: no model' >&2\nexit 3\n")
    engine = reconstruct.ColmapReconstructor(script=bad, timeout_s=5)
    with pytest.raises(StageError) as ei:
        engine.reconstruct(tmp_path, tmp_path / "work")
    assert "exited 3" in str(ei.value) and "no model" in str(ei.value)


def test_worker_module_imports():
    import worker  # noqa: F401

    assert Path(worker.__file__).name == "worker.py"
