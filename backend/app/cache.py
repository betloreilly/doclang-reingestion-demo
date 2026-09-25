"""Safe local cache for MinIO object downloads."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import Settings, get_settings


class PathEscapeError(ValueError):
    """Raised when an object key would escape the cache root."""


def safe_cache_path(cache_root: Path, object_key: str) -> Path:
    """Map an object key to a path under cache_root without allowing traversal."""
    if not object_key or object_key.endswith("/"):
        raise PathEscapeError(f"Invalid object key: {object_key!r}")
    # Reject absolute keys before normalizing separators.
    if object_key.startswith("/") or object_key.startswith("\\") or Path(object_key).is_absolute():
        raise PathEscapeError(f"Absolute object key not allowed: {object_key!r}")
    if ":" in object_key.split("/", 1)[0] and len(object_key) > 2 and object_key[1] == ":":
        # Windows drive letter paths
        raise PathEscapeError(f"Absolute object key not allowed: {object_key!r}")

    key = object_key.lstrip("/")
    parts = Path(key).parts
    if any(p in ("", ".", "..") for p in parts):
        raise PathEscapeError(f"Object key escapes cache directory: {object_key!r}")

    root = cache_root.resolve()
    candidate = (root / key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(
            f"Object key escapes cache directory: {object_key!r}"
        ) from exc
    return candidate


class ArtifactCache:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.cache_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, object_key: str) -> Path:
        return safe_cache_path(self.root, object_key)

    def has(self, object_key: str) -> bool:
        path = self.path_for(object_key)
        return path.is_file() and path.stat().st_size > 0

    def ensure_parent(self, object_key: str) -> Path:
        path = self.path_for(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
