"""Application configuration loaded from backend/.env."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


def parse_bool(value: object) -> bool:
    """Parse common boolean string forms explicitly (no silent truthiness)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off", ""}:
        return False
    raise ValueError(f"Cannot parse boolean from {value!r}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    minio_endpoint: str = Field(default="", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", alias="MINIO_SECRET_KEY")
    minio_session_token: str = Field(default="", alias="MINIO_SESSION_TOKEN")
    minio_secure: bool = Field(default=True, alias="MINIO_SECURE")
    minio_bucket: str = Field(default="", alias="MINIO_BUCKET")
    minio_prefix: str = Field(default="", alias="MINIO_PREFIX")

    data_dir: Path = Field(default=BACKEND_ROOT / "data", alias="DATA_DIR")
    cache_dir: Path = Field(default=BACKEND_ROOT / "data" / "cache", alias="CACHE_DIR")
    runs_dir: Path = Field(default=BACKEND_ROOT / "data" / "runs", alias="RUNS_DIR")
    artifacts_dir: Path = Field(
        default=BACKEND_ROOT / "data" / "artifacts", alias="ARTIFACTS_DIR"
    )
    # Optional extra folder of prepared artifacts (mirrors the remote prefix layout).
    # Example: ./data/local containing docs/dclx/*.dclx
    local_docs_dir: Path = Field(
        default=BACKEND_ROOT / "data" / "local", alias="LOCAL_DOCS_DIR"
    )

    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    embedding_model: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B", alias="EMBEDDING_MODEL"
    )

    @field_validator("minio_secure", mode="before")
    @classmethod
    def _secure_bool(cls, value: object) -> bool:
        return parse_bool(value)

    @field_validator(
        "data_dir",
        "cache_dir",
        "runs_dir",
        "artifacts_dir",
        "local_docs_dir",
        mode="before",
    )
    @classmethod
    def _resolve_path(cls, value: object) -> Path:
        path = Path(str(value) if value is not None else ".")
        if not path.is_absolute():
            path = (BACKEND_ROOT / path).resolve()
        return path

    @field_validator("minio_prefix", mode="before")
    @classmethod
    def _normalize_prefix(cls, value: object) -> str:
        text = str(value or "").lstrip("/")
        if text and not text.endswith("/"):
            text += "/"
        return text

    @field_validator("minio_endpoint", mode="before")
    @classmethod
    def _strip_scheme(cls, value: object) -> str:
        text = str(value or "").strip()
        for prefix in ("https://", "http://"):
            if text.lower().startswith(prefix):
                text = text[len(prefix) :]
        return text.rstrip("/")

    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.cache_dir,
            self.runs_dir,
            self.artifacts_dir,
            self.local_docs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def minio_configured(self) -> bool:
        placeholders = {
            "",
            "your-minio-host:9000",
            "your-access-key",
            "your-secret-key",
            "your-bucket",
        }
        if self.minio_endpoint in placeholders or not self.minio_endpoint:
            return False
        if self.minio_access_key in placeholders or not self.minio_access_key:
            return False
        if self.minio_secret_key in placeholders or not self.minio_secret_key:
            return False
        if self.minio_bucket in placeholders or not self.minio_bucket:
            return False
        return True

    def configuration_errors(self) -> List[str]:
        errors: List[str] = []
        if not self.minio_configured():
            if not self.minio_endpoint or self.minio_endpoint == "your-minio-host:9000":
                errors.append(
                    "MINIO_ENDPOINT is missing or still a placeholder. Put the real host "
                    "in backend/.env (host:port, no scheme)."
                )
            elif "://" in self.minio_endpoint:
                errors.append(
                    "MINIO_ENDPOINT must not include a URL scheme; set MINIO_SECURE instead."
                )
            if not self.minio_access_key or self.minio_access_key == "your-access-key":
                errors.append(
                    "MINIO_ACCESS_KEY is missing or still a placeholder. Set it in backend/.env."
                )
            if not self.minio_secret_key or self.minio_secret_key == "your-secret-key":
                errors.append(
                    "MINIO_SECRET_KEY is missing or still a placeholder. Set it in backend/.env."
                )
            if not self.minio_bucket or self.minio_bucket == "your-bucket":
                errors.append(
                    "MINIO_BUCKET is missing or still a placeholder. Set it in backend/.env."
                )
            return errors
        if "://" in self.minio_endpoint:
            errors.append(
                "MINIO_ENDPOINT must not include a URL scheme; set MINIO_SECURE instead."
            )
        return errors


@lru_cache
def get_settings() -> Settings:
    # Allow tests to inject env before first load
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def env_file_present() -> bool:
    return ENV_FILE.exists() or bool(os.getenv("MINIO_ENDPOINT"))
