"""Unit tests for config, pairing, cost, cache safety, and chunking."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.cache import PathEscapeError, safe_cache_path
from app.chunking import chunk_markdown
from app.config import Settings, parse_bool
from app.cost import compute_costs, compute_time_savings
from app.pairing import pair_objects, strip_known_suffixes
from app.schemas import CostSettings, ObjectInfo
from app.tokens import count_paid_tokens


def test_parse_bool_explicit():
    assert parse_bool("true") is True
    assert parse_bool("TRUE") is True
    assert parse_bool("1") is True
    assert parse_bool("false") is False
    assert parse_bool("0") is False
    assert parse_bool("") is False
    with pytest.raises(ValueError):
        parse_bool("maybe")


def test_settings_strips_scheme(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", "https://example.com:9000")
    monkeypatch.setenv("MINIO_SECURE", "true")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")
    monkeypatch.setenv("MINIO_BUCKET", "demo-bucket")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    s = Settings()
    assert s.minio_endpoint == "example.com:9000"
    assert s.minio_secure is True
    assert s.configuration_errors() == []


def test_strip_compound_suffix():
    assert strip_known_suffixes("report.pdf.dclx") == "report"
    assert strip_known_suffixes("report.dclx") == "report"
    assert strip_known_suffixes("report.pdf") == "report"


def test_pairing_ambiguous_and_compound():
    objects = [
        ObjectInfo(key="docs/dclx/Acme_2023.pdf.dclx", size=10),
        ObjectInfo(key="docs/pdf/Acme_2023.pdf", size=20),
        ObjectInfo(key="docs/pdf/Acme_2023_alt.pdf", size=21),
        ObjectInfo(key="docs/json/Acme_2023.json", size=5),
        ObjectInfo(key="docs/dclx/OnlyDoc.dclx", size=8),
    ]
    # Force ambiguous by same stem — alt won't match Acme_2023 stem
    objects.append(ObjectInfo(key="docs/pdf/Acme_2023.copy.pdf", size=22))
    # Manually create two PDFs with same stem via renaming trick:
    objects = [
        ObjectInfo(key="docs/dclx/Acme.pdf.dclx", size=10),
        ObjectInfo(key="docs/pdf/Acme.pdf", size=20),
        ObjectInfo(key="docs/pdf/subdir/Acme.pdf", size=21),
        ObjectInfo(key="docs/dclx/Solo.dclx", size=3),
    ]
    pairs = pair_objects(objects)
    by_name = {p.document_name: p for p in pairs}
    assert "Acme" in by_name
    assert by_name["Acme"].pairing_status == "ambiguous_pdf"
    assert len(by_name["Acme"].ambiguous_pdf_keys) == 2
    assert by_name["Solo"].pairing_status == "dclx_only"
    assert by_name["Solo"].pdf is None


def test_safe_cache_path(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    ok = safe_cache_path(root, "docs/dclx/a.dclx")
    assert str(ok).startswith(str(root.resolve()))
    with pytest.raises(PathEscapeError):
        safe_cache_path(root, "../etc/passwd")
    with pytest.raises(PathEscapeError):
        safe_cache_path(root, "/absolute/path.dclx")


def test_chunking_repeats_table_headers():
    md = """# Revenue

| Period | USD |
| --- | --- |
| 2021 | 10 |
| 2022 | 20 |
| 2023 | 30 |
| 2024 | 40 |
"""
    chunks, _ = chunk_markdown(md, "doc1", chunk_size=60, chunk_overlap=10)
    assert chunks
    header_hits = sum(1 for c in chunks if "Period" in c.text and "USD" in c.text)
    assert header_hits >= 1
    # Overlap / repeated headers should increase total chars vs unique rows alone
    total_chars = sum(len(c.text) for c in chunks)
    assert total_chars > len("2021 10 2022 20 2023 30 2024 40")


def test_chunking_applies_overlap_and_repeats_heading():
    paras = [f"P{i:02d} " + ("x" * 300) for i in range(20)]
    md = "# Section\n\n" + "\n\n".join(paras)
    chunks, _ = chunk_markdown(md, "doc1", chunk_size=1000, chunk_overlap=150)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1000 for c in chunks)
    # Every chunk carries new content, never only the overlap tail.
    assert all(any(f"P{i:02d}" in c.text for i in range(20)) for c in chunks)
    assert all("# Section" in c.text for c in chunks)
    # Overlap duplicates text, so packed text is longer than the source.
    assert sum(len(c.text) for c in chunks) > len(md)

    no_overlap, _ = chunk_markdown(md, "doc1", chunk_size=1000, chunk_overlap=0)
    assert sum(len(c.text) for c in no_overlap) < sum(len(c.text) for c in chunks)


def test_chunking_compacts_table_padding():
    md = (
        "| Name      | Value    |\n"
        "|-----------|----------|\n"
        "| alpha     | 1        |\n"
    )
    chunks, _ = chunk_markdown(md, "doc1", chunk_size=500, chunk_overlap=0)
    assert chunks[0].text == "| Name | Value |\n|---|---|\n| alpha | 1 |"


def test_process_settings_rejects_large_overlap():
    from pydantic import ValidationError

    from app.schemas import ProcessSettings

    ProcessSettings(chunk_size=1000, chunk_overlap=500)
    with pytest.raises(ValidationError):
        ProcessSettings(chunk_size=1000, chunk_overlap=501)


def test_manual_pdf_pairing_resets_pdf_page_count():
    from app.pairing import apply_manual_pairing, index_objects

    objs = [
        ObjectInfo(key="a/report.dclx", size=1),
        ObjectInfo(key="a/report.pdf", size=1),
        ObjectInfo(key="a/other.pdf", size=1),
    ]
    pairs = pair_objects(objs)
    pair = pairs[0]
    pair.page_count, pair.page_count_source = 10, "pdf"
    apply_manual_pairing(pairs, pair.document_id, "a/other.pdf", index_objects(objs))
    assert pair.pdf.key == "a/other.pdf"
    assert pair.page_count is None

    apply_manual_pairing(pairs, pair.document_id, None, index_objects(objs), page_count=7)
    apply_manual_pairing(pairs, pair.document_id, "a/report.pdf", index_objects(objs))
    assert pair.page_count == 7
    assert pair.page_count_source == "manual"


def test_cost_zero_reingestions_and_missing_price():
    cost = compute_costs(
        pages=2000,
        paid_tokens=100_000,
        settings=CostSettings(
            extraction_price_per_1000_pages=4.0,
            embedding_price_per_million_tokens=None,
            future_reingestions=0,
        ),
    )
    assert cost.avoided_extraction_per_reingestion == 8.0
    assert cost.future_extraction_savings == 0.0
    assert cost.embedding_pricing_available is False
    assert cost.hypothetical_fresh_pdf_cost is None
    assert cost.prepared_doclang_cost is None
    assert cost.extraction_to_embedding_ratio is None

    cost2 = compute_costs(
        pages=1000,
        paid_tokens=1_000_000,
        settings=CostSettings(
            extraction_price_per_1000_pages=4.0,
            embedding_price_per_million_tokens=0.10,
            future_reingestions=3,
        ),
    )
    assert cost2.avoided_extraction_per_reingestion == 4.0
    assert cost2.embedding_estimate_per_reingestion == pytest.approx(0.10)
    assert cost2.hypothetical_fresh_pdf_cost == pytest.approx(4.10)
    assert cost2.prepared_doclang_cost == pytest.approx(0.10)
    assert cost2.extraction_to_embedding_ratio == pytest.approx(40.0)
    assert cost2.embedding_pct_of_extraction == pytest.approx(2.5)
    assert cost2.extraction_pct_of_fresh_total == pytest.approx(4.0 / 4.1 * 100.0)
    assert cost2.embedding_pct_of_fresh_total == pytest.approx(0.10 / 4.1 * 100.0)


def test_time_savings_unavailable_without_baseline():
    ts = compute_time_savings(1.5, 3.0, CostSettings(extraction_baseline_seconds=None))
    assert ts.available is False
    assert ts.estimated_extraction_stage_savings_seconds is None

    ts2 = compute_time_savings(
        1.5, 3.0, CostSettings(extraction_baseline_seconds=10.0, extraction_baseline_note="lab")
    )
    assert ts2.available is True
    assert ts2.estimated_extraction_stage_savings_seconds == pytest.approx(8.5)
    assert ts2.modeled_full_pipeline_fresh_seconds == pytest.approx(13.0)
    assert ts2.modeled_full_pipeline_prepared_seconds == pytest.approx(4.5)


def test_paid_token_methods_labeled():
    text = "hello world " * 20
    approx = count_paid_tokens(text, "chars_div_4")
    assert approx.exact is False
    assert "approx" in approx.tokenizer_name or "approximate" in approx.method

    same = count_paid_tokens(text, "same_as_local", local_count=42)
    assert same.token_count == 42
    assert same.exact is False
