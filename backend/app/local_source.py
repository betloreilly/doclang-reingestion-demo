"""Discover prepared DocLang/PDF files from local disk (no MinIO)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Set

from .schemas import ObjectInfo


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def scan_local_objects(*roots: Path) -> List[ObjectInfo]:
    """
    Scan one or more local roots for .dclx / .pdf / .json files.

    Object keys are paths relative to each root (posix), so they stay stable
    with the MinIO layout when files live under cache/docs/...
    """
    seen: Set[str] = set()
    objects: List[ObjectInfo] = []
    for root in roots:
        root = root.resolve()
        if not root.exists():
            continue
        for path in _iter_files(root):
            suffix = path.suffix.lower()
            name = path.name.lower()
            if not (
                name.endswith(".dclx")
                or suffix == ".pdf"
                or suffix == ".json"
            ):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel in seen:
                continue
            seen.add(rel)
            objects.append(
                ObjectInfo(
                    key=rel,
                    size=path.stat().st_size,
                )
            )
    objects.sort(key=lambda o: o.key)
    return objects


def resolve_local_path(roots: List[Path], object_key: str) -> Optional[Path]:
    """Find an object key under the first matching local root."""
    key = object_key.lstrip("/")
    for root in roots:
        candidate = (root / key).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
