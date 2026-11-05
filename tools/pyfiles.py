"""The Python files of a project. Every tool's file scan goes through here, so
an environment inside the project is never read as the project's code."""
import os
from pathlib import Path


def is_env(folder):
    """Whether folder is a Python environment: a virtualenv, a venv or a conda env."""
    folder = Path(folder)
    return (folder / "pyvenv.cfg").exists() or (folder / "conda-meta").is_dir()


def py_files(root, skip=()):
    """Every Python file under root, sorted, minus __pycache__, environments and
    the paths in skip (as given, relative to the current folder). A left-out
    folder is never entered."""
    skip = {Path(e) for e in skip}
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not is_env(Path(base, d)) and Path(base, d) not in skip]
        found += [p for p in (Path(base, f) for f in files if f.endswith(".py")) if p not in skip]
    return sorted(found)
