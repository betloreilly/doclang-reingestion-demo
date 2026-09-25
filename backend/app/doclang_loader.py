"""Load prepared DocLang (.dclx) archives — no PDF extraction."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class DocLangLoadError(RuntimeError):
    """Actionable failure while deserializing a prepared DocLang archive."""


def load_doclang_archive(
    dclx_path: Path,
    artifacts_dir: Optional[Path] = None,
) -> Tuple[Any, float, Dict[str, Any]]:
    """
    Deserialize an existing DocLang OPC archive.

    Uses DoclingDocument.load_from_doclang_archive — this path loads an already
    prepared representation and does not run PDF extraction, OCR, or layout inference.
    """
    from docling_core.types.doc import DoclingDocument

    if not dclx_path.is_file():
        raise DocLangLoadError(
            f"DocLang archive not found at {dclx_path}. Download the .dclx object first."
        )

    if artifacts_dir is None:
        artifacts_dir = dclx_path.parent / f"{dclx_path.stem}_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    try:
        document = DoclingDocument.load_from_doclang_archive(
            dclx_path,
            artifacts_dir=artifacts_dir,
        )
    except Exception as exc:  # noqa: BLE001
        raise DocLangLoadError(
            "Failed to load prepared DocLang archive "
            f"'{dclx_path.name}' via DoclingDocument.load_from_doclang_archive. "
            "Confirm the object is a .dclx DocLang OPC archive compatible with "
            f"docling-core, and that the file is not corrupt. Underlying error: "
            f"{type(exc).__name__}: {exc}. "
            "This application will not fall back to JSON or PDF extraction."
        ) from exc
    elapsed = time.perf_counter() - started

    meta = {
        "loader": "DoclingDocument.load_from_doclang_archive",
        "path": str(dclx_path),
        "artifacts_dir": str(artifacts_dir),
        "name": getattr(document, "name", dclx_path.stem),
        "pdf_extraction": False,
        "ocr": False,
        "layout_inference": False,
    }
    return document, elapsed, meta


def document_to_markdown(document: Any) -> str:
    try:
        return document.export_to_markdown()
    except Exception:  # noqa: BLE001
        try:
            return document.export_to_text()
        except Exception as exc:  # noqa: BLE001
            raise DocLangLoadError(
                f"Loaded DocLang document but could not export text: {exc}"
            ) from exc
