"""Lightweight PDF page counting (no Docling / OCR)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from pypdf import PdfReader


def count_pdf_pages(pdf_path: Path) -> Tuple[Optional[int], str]:
    """Return (page_count, source_label). Never invokes Docling."""
    try:
        reader = PdfReader(str(pdf_path), strict=False)
        return len(reader.pages), "pdf"
    except Exception as exc:  # noqa: BLE001
        return None, f"pdf_error:{type(exc).__name__}"


def pages_from_doclang_document(document) -> Tuple[Optional[int], str]:
    """Use prepared DocLang page map when present."""
    try:
        pages = getattr(document, "pages", None)
        if pages is None:
            return None, "unavailable"
        if isinstance(pages, dict):
            count = len(pages)
        else:
            count = len(list(pages))
        if count > 0:
            return count, "doclang_pages"
        return None, "unavailable"
    except Exception:  # noqa: BLE001
        return None, "unavailable"
