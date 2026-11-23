"""CONVI workspace layout.

The tool lives in a workspace with three top-level folders::

    <CONVI>/Tool/Convi             this tool
    <CONVI>/Projects/<Name>/...    KiCad source projects (one folder or repository per project)
    <CONVI>/Outputs/convi_out/...  everything Convi writes (one folder per conversion)

Nothing here is hard-coded to a drive or user folder: the workspace root is the first parent folder
(of the KiCad project, then of this tool) that contains ``Tool``, ``Projects`` and ``Outputs``.
``CONVI_WORKSPACE`` overrides the search.  Outside a workspace Convi falls back to the old default
``<project>/Altium``.
"""
from __future__ import annotations

import os
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
MARKERS = ("Tool", "Projects", "Outputs")
CONVI_OUT = "convi_out"
# folders searched automatically for STEP models inside a project (e.g. MyBoard/hardware/libraries/3dmodels)
MODEL_DIR_NAMES = {"3dmodels", "3d_models", "3d-models", "packages3d"}
_SKIP_DIRS = {".git", ".history", "__pycache__", ".pytest_cache", "node_modules"}


def is_workspace(p: Path) -> bool:
    return all((p / m).is_dir() for m in MARKERS)


def find_workspace(start: Path | str | None = None) -> Path | None:
    """CONVI root: CONVI_WORKSPACE, else the first parent of `start`, else of this tool, with Tool/Projects/Outputs."""
    env = os.environ.get("CONVI_WORKSPACE")
    if env and is_workspace(Path(env)):
        return Path(env).resolve()
    bases = ([Path(start).resolve()] if start else []) + [TOOL_DIR]
    for base in bases:
        for p in (base, *base.parents):
            if is_workspace(p):
                return p
    return None


def project_root(folder: Path | str, ws: Path | None = None) -> Path:
    """<CONVI>/Projects/<Name> for a KiCad project inside the workspace, else the KiCad project folder."""
    folder = Path(folder).resolve()
    ws = ws or find_workspace(folder)
    if ws:
        try:
            rel = folder.relative_to(ws / "Projects")
            if rel.parts:
                return ws / "Projects" / rel.parts[0]
        except ValueError:
            pass
    return folder


def project_name(folder: Path | str, ws: Path | None = None) -> str:
    """Name used for the output folder: the folder under Projects (MyBoard), else the KiCad project folder name."""
    return project_root(folder, ws).name


def outputs_dir(ws: Path | None = None) -> Path | None:
    ws = ws or find_workspace()
    return ws / "Outputs" / CONVI_OUT if ws else None


def default_out_dir(folder: Path | str) -> Path:
    """<CONVI>/Outputs/convi_out/<Name>; an existing, non-empty conversion is never reused: <Name>_v2, _v3, ...

    Outside a workspace: <project>/Altium (previous behaviour)."""
    folder = Path(folder).resolve()
    ws = find_workspace(folder)
    if ws is None:
        return folder / "Altium"
    base = ws / "Outputs" / CONVI_OUT
    name = project_name(folder, ws)
    cand, n = base / name, 2
    while cand.exists() and any(cand.iterdir()):
        cand, n = base / f"{name}_v{n}", n + 1
    return cand


def default_model_dirs(folder: Path | str, max_depth: int = 6) -> list[Path]:
    """STEP model folders inside the project (e.g. hardware/libraries/3dmodels), searched automatically."""
    root = project_root(folder)
    found: list[Path] = []
    if not root.is_dir():
        return found
    base_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        p = Path(dirpath)
        if len(p.parts) - base_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.endswith("-backups")]
        for d in dirnames:
            if d.lower() in MODEL_DIR_NAMES:
                found.append(p / d)
    return sorted(found)


def dev_out_dir() -> Path:
    """Output folder of the development scripts (devtools): <CONVI>/Outputs/convi_out/_dev, else <tool>/output."""
    out = outputs_dir()
    return out / "_dev" if out else TOOL_DIR / "output"
