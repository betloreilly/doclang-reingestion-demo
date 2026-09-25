"""Integration: load prepared .dclx with PDF access disabled."""

from __future__ import annotations

from pathlib import Path

from docling_core.types.doc import DocItemLabel, DoclingDocument

from app.chunking import chunk_document
from app.doclang_loader import load_doclang_archive
from app.tokens import LocalTokenizer, count_paid_tokens


def test_prepared_doclang_roundtrip_without_pdf(tmp_path: Path):
    dclx = tmp_path / "demo.pdf.dclx"
    doc = DoclingDocument(name="demo")
    doc.add_text(label=DocItemLabel.TITLE, text="Income Statement")
    doc.add_text(
        label=DocItemLabel.PARAGRAPH,
        text="Reporting period FY2022. Currency USD millions.",
    )
    doc.add_text(
        label=DocItemLabel.PARAGRAPH,
        text="Net income increased year over year.",
    )
    doc.save_as_doclang_archive(dclx)

    loaded, elapsed, meta = load_doclang_archive(
        dclx, artifacts_dir=tmp_path / "arts"
    )
    assert elapsed >= 0
    assert meta["pdf_extraction"] is False
    assert meta["ocr"] is False

    chunks, markdown, _ = chunk_document(
        loaded, "demo", chunk_size=80, chunk_overlap=20
    )
    assert "FY2022" in markdown or any("FY2022" in c.text for c in chunks)
    # Overlap can repeat content across chunks when size is small
    joined = "\n".join(c.text for c in chunks)
    assert "USD" in joined

    # Truncation detection surfaces long inputs
    tok = LocalTokenizer.__new__(LocalTokenizer)
    tok.model_name = "dummy"
    tok._tokenizer = None
    tok.max_length = 5

    class FakeTok:
        def __call__(self, text, **kwargs):
            return {"input_ids": list(range(20))}

    tok._ensure = lambda: FakeTok()  # type: ignore
    result = tok.count("x")
    assert result.truncated is True
    assert result.truncation_note

    paid = count_paid_tokens("hello", "chars_div_4")
    assert paid.exact is False
