"""Synthetic DocLang-ish markdown loading path without PDF extraction."""

from __future__ import annotations

from pathlib import Path

from app.chunking import chunk_markdown
from app.doclang_loader import DocLangLoadError, load_doclang_archive


def test_load_missing_archive_errors_clearly(tmp_path: Path):
    missing = tmp_path / "nope.dclx"
    try:
        load_doclang_archive(missing)
        assert False, "expected DocLangLoadError"
    except DocLangLoadError as exc:
        assert "not found" in str(exc).lower() or "DocLang" in str(exc)


def test_chunk_path_does_not_need_pdf():
    # Processing path works from markdown alone (PDF access disabled).
    md = "# Filing\n\nCurrency: USD\n\n| Item | FY2023 |\n| --- | --- |\n| Cash | 100 |\n"
    chunks, elapsed = chunk_markdown(md, "x", chunk_size=200, chunk_overlap=20)
    assert elapsed >= 0
    assert any("USD" in c.text or "FY2023" in c.text for c in chunks)
