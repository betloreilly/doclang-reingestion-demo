"""Read-only MinIO client helpers. Credentials never logged."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from minio import Minio
from minio.error import S3Error

from .config import Settings, env_file_present, get_settings
from .schemas import ConnectionStatus, ObjectInfo


def _client_from_settings(settings: Settings) -> Minio:
    kwargs = {
        "endpoint": settings.minio_endpoint,
        "access_key": settings.minio_access_key,
        "secret_key": settings.minio_secret_key,
        "secure": settings.minio_secure,
    }
    if settings.minio_session_token:
        kwargs["session_token"] = settings.minio_session_token
    # Verified TLS when secure=True (cert_check defaults to True).
    return Minio(**kwargs)


class MinioService:
    """Read-only operations against the configured bucket/prefix."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._client: Optional[Minio] = None

    @property
    def client(self) -> Minio:
        if self._client is None:
            errors = self.settings.configuration_errors()
            if errors:
                raise RuntimeError("; ".join(errors))
            self._client = _client_from_settings(self.settings)
        return self._client

    def status(self) -> ConnectionStatus:
        settings = self.settings
        errors = settings.configuration_errors()
        connected = False
        counts: Dict[str, int] = {}
        if not errors:
            try:
                exists = self.client.bucket_exists(settings.minio_bucket)
                if not exists:
                    errors.append(
                        f"Bucket '{settings.minio_bucket}' was not found or is inaccessible."
                    )
                else:
                    objects = self.list_objects()
                    connected = True
                    counts = {
                        "total": len(objects),
                        "dclx": sum(1 for o in objects if "/dclx/" in o.key or o.key.endswith(".dclx")),
                        "pdf": sum(1 for o in objects if "/pdf/" in o.key or o.key.lower().endswith(".pdf")),
                        "json": sum(1 for o in objects if "/json/" in o.key or o.key.lower().endswith(".json")),
                    }
            except S3Error as exc:
                # Do not include credentials; MinIO error codes are safe.
                errors.append(f"MinIO S3 error ({exc.code}): {exc.message}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"MinIO connection failed: {type(exc).__name__}: {exc}")

        return ConnectionStatus(
            configured=settings.minio_configured(),
            connected=connected and not errors,
            bucket=settings.minio_bucket,
            prefix=settings.minio_prefix,
            endpoint_host=settings.minio_endpoint,
            secure=settings.minio_secure,
            env_file_present=env_file_present(),
            errors=errors,
            object_counts=counts,
        )

    def list_objects(self, prefix: Optional[str] = None) -> List[ObjectInfo]:
        use_prefix = prefix if prefix is not None else self.settings.minio_prefix
        results: List[ObjectInfo] = []
        for obj in self.client.list_objects(
            self.settings.minio_bucket, prefix=use_prefix, recursive=True
        ):
            if obj.is_dir:
                continue
            last_modified = None
            if isinstance(obj.last_modified, datetime):
                last_modified = obj.last_modified.isoformat()
            results.append(
                ObjectInfo(
                    key=obj.object_name,
                    size=int(obj.size or 0),
                    last_modified=last_modified,
                    etag=obj.etag,
                )
            )
        return results

    def object_exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.settings.minio_bucket, key)
            return True
        except S3Error:
            return False

    def download_object(self, key: str, dest_path) -> Tuple[int, float]:
        """Download object to dest_path. Returns (bytes_written, elapsed_seconds)."""
        import time
        from pathlib import Path

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        self.client.fget_object(self.settings.minio_bucket, key, str(dest))
        elapsed = time.perf_counter() - started
        size = dest.stat().st_size if dest.exists() else 0
        return size, elapsed
