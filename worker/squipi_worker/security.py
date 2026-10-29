"""File access policy: the worker reads only what the app has explicitly allowed.

The app allows a path when the user drops a file on Squipi or picks it in an open panel.
Allowing a file allows exactly that file; allowing a directory allows everything beneath it.
Everything is resolved (symlinks included) before comparison, so a link cannot escape a root.
"""
from __future__ import annotations

import os
from pathlib import Path


class AccessDenied(PermissionError):
    pass


class AllowList:
    def __init__(self) -> None:
        self._files: set[Path] = set()
        self._dirs: set[Path] = set()

    def allow(self, path: str | os.PathLike[str]) -> Path:
        p = Path(path).expanduser().resolve()
        if p.is_dir():
            self._dirs.add(p)
        else:
            self._files.add(p)
        return p

    def allowed(self) -> list[str]:
        return sorted(str(p) for p in self._files | self._dirs)

    def check_readable(self, path: str | os.PathLike[str]) -> Path:
        p = Path(path).expanduser().resolve()
        if p in self._files:
            return p
        for d in self._dirs:
            try:
                p.relative_to(d)
                return p
            except ValueError:
                continue
        raise AccessDenied(f"{p} is not a file Squipi was given. Drop it on Squipi or pick it in the workspace first.")


def ensure_within(path: Path, root: Path) -> Path:
    """Outputs must stay inside the project directory."""
    p = path.resolve()
    root = root.resolve()
    try:
        p.relative_to(root)
    except ValueError as e:
        raise AccessDenied(f"refusing to write outside the project directory: {p}") from e
    return p
