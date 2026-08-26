"""Fixture discovery + loading for the verification harness (P2-1).

A fixture is one physical test model under `fixtures/<name>/` (see fixtures/README).
The harness needs three things from each: the reconstructed **mesh**, the caliper
**truth**, and the **scale** (scene units → mm). Slice **params** are optional and
only used by manual-slice methods (the P2-2+ auto methods derive their own).

    fixtures/<name>/
      mesh.obj      required  reconstruction output
      truth.json    required  caliper measurement at the FR-10 reference point
      scale.json    optional  {"scale_mm_per_scene_unit": <float>}  (default 1.0)
      params.json   optional  manual slice params for baseline methods
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


class FixtureError(RuntimeError):
    pass


@dataclass
class Fixture:
    name: str
    path: Path
    truth: dict[str, Any]
    scale: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def mesh_path(self) -> Path:
        return self.path / "mesh.obj"

    @property
    def truth_mm(self) -> float:
        """Caliper truth value. Accepts `diameter_mm` or generic `value_mm`."""
        for key in ("diameter_mm", "value_mm", "truth_mm"):
            if key in self.truth:
                return float(self.truth[key])
        raise FixtureError(f"{self.name}: truth.json needs a diameter_mm/value_mm field")

    @property
    def metric(self) -> str:
        return str(self.truth.get("metric", "diameter"))

    @property
    def scale_mm_per_unit(self) -> float:
        return float(self.scale.get("scale_mm_per_scene_unit", 1.0))

    def load_mesh(self) -> tuple[np.ndarray, np.ndarray]:
        return load_mesh(self.mesh_path)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return json.load(fh)


def load_fixture(directory: Path) -> Fixture:
    truth_path = directory / "truth.json"
    if not truth_path.exists():
        raise FixtureError(f"{directory.name}: missing truth.json")
    scale = _read_json(directory / "scale.json") if (directory / "scale.json").exists() else {}
    params = _read_json(directory / "params.json") if (directory / "params.json").exists() else {}
    return Fixture(
        name=directory.name,
        path=directory,
        truth=_read_json(truth_path),
        scale=scale,
        params=params,
    )


def discover_fixtures(root: Path) -> list[Fixture]:
    """All `<root>/<name>/` dirs that have both mesh.obj and truth.json."""
    if not root.exists():
        return []
    fixtures: list[Fixture] = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if (directory / "mesh.obj").exists() and (directory / "truth.json").exists():
            fixtures.append(load_fixture(directory))
    return fixtures


def load_mesh(mesh_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Load an OBJ into (vertices (V,3), faces (F,3)). trimesh handles scenes."""
    import trimesh  # lazy — part of the `measure` extra

    if not mesh_path.exists():
        raise FixtureError(f"missing mesh: {mesh_path}")
    mesh = trimesh.load(mesh_path, force="mesh", process=False)
    return np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces, dtype=int)
