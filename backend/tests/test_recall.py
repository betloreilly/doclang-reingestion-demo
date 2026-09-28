"""Unit tests for the FinanceBench recall comparison (no network, no OpenAI calls)."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from app.recall.dataset import parse_question
from app.recall.embed import OpenAIEmbedder, load_cached, save_cached, texts_hash
from app.recall.extract import (
    align_to_pdf_pages,
    evidence_coverage,
    page_chunks,
    unstructured_pages,
)
from app.recall.metrics import global_topk, local_topk, score_ranking, summarize
from app.recall.runner import RecallRequest


def test_parse_question_converts_zero_indexed_pages():
    q = parse_question(
        {
            "financebench_id": "fb_1",
            "doc_name": "ACME_2020_10K",
            "question_type": "metrics-generated",
            "question": "What was revenue?",
            "answer": "$1",
            "evidence": [{"evidence_text": "Revenue 1", "evidence_page_num": 59}],
        }
    )
    assert q.evidence[0].page == 60
    assert q.evidence_pages() == {("ACME_2020_10K", 60)}


def test_unstructured_pages_groups_and_marks_titles():
    pages = unstructured_pages(
        {
            "elements": [
                {"type": "Title", "text": "Cash Flows", "page_number": 2},
                {"type": "NarrativeText", "text": "Net income 100", "page_number": 2},
                {"type": "ListItem", "text": "item", "page_number": 3},
                {"type": "NarrativeText", "text": "   ", "page_number": 3},
            ]
        }
    )
    assert pages[2] == "## Cash Flows\n\nNet income 100"
    assert pages[3] == "- item"


def test_page_chunks_stay_on_one_page_and_carry_heading():
    pages = {1: "## Revenue\n\n" + "alpha " * 50, 2: "beta " * 50}
    chunks = page_chunks(pages, "DOC", chunk_size=200, chunk_overlap=20)
    assert {c.page for c in chunks} == {1, 2}
    page2 = [c for c in chunks if c.page == 2]
    assert all("alpha" not in c.text for c in page2)
    assert page2[0].text.startswith("## Revenue")


def test_align_to_pdf_pages_recovers_dropped_page():
    pdf = ["cover page acme", "", "revenue grew strongly this year", "cash flow statement details"]
    archive = {1: "cover page acme", 2: "revenue grew strongly this year", 3: "cash flow statement details"}
    remapped, shifted = align_to_pdf_pages(archive, pdf)
    assert remapped == {1: "cover page acme", 3: "revenue grew strongly this year", 4: "cash flow statement details"}
    assert shifted == 2
    same, none_shifted = align_to_pdf_pages({1: "a b", 2: "c d"}, ["a b", "c d"])
    assert same == {1: "a b", 2: "c d"} and none_shifted == 0


def test_evidence_coverage_normalizes_numbers():
    assert evidence_coverage("Net sales $32,765", "net sales were 32765 million") == 1.0
    assert evidence_coverage("capital expenditures 1,577", "unrelated text") == 0.0
    assert evidence_coverage("", "anything") == 1.0


def test_score_ranking_and_summary():
    evidence = {("D", 3), ("D", 4)}
    ranked = [("D", 1), ("D", 3), ("E", 3), ("D", 4)]
    scored = score_ranking(ranked, evidence)
    assert scored["first_rank"] == 2
    assert scored["recall_at"][1] == 0.0
    assert scored["recall_at"][3] == 0.5
    assert scored["recall_at"][5] == 1.0
    summary = summarize(
        [
            {**scored, "question_type": "a"},
            {"first_rank": None, "recall_at": {1: 0, 3: 0, 5: 0, 10: 0}, "question_type": "b"},
        ]
    )
    assert summary["overall"]["hit_at"]["1"] == 0.0
    assert summary["overall"]["hit_at"]["3"] == 0.5
    assert summary["overall"]["mrr"] == pytest.approx(0.25)
    assert summary["by_type"]["a"]["hit_at"]["3"] == 1.0


def test_score_ranking_counts_distinct_pages():
    evidence = {("D", 5)}
    ranked = [("D", 1), ("D", 1), ("D", 1), ("D", 2), ("D", 2), ("D", 5)]
    scored = score_ranking(ranked, evidence)
    assert scored["first_rank"] == 3
    assert scored["recall_at"][1] == 0.0
    assert scored["recall_at"][3] == 1.0


def test_global_topk_matches_brute_force():
    rng = np.random.default_rng(0)
    queries = rng.normal(size=(4, 8)).astype(np.float32)
    docs = [rng.normal(size=(n, 8)).astype(np.float32) for n in (3, 0, 12, 7)]
    scores, doc_idx, chunk_idx = global_topk(queries, enumerate(docs), k=5)
    allv = np.concatenate([d for d in docs if len(d)])
    owners = [(di, ci) for di, d in enumerate(docs) for ci in range(len(d))]
    for qi in range(4):
        sims = allv @ queries[qi]
        expected = [owners[i] for i in np.argsort(-sims)[:5]]
        assert list(zip(doc_idx[qi], chunk_idx[qi])) == expected
        assert np.all(np.diff(scores[qi]) <= 0)
    s, idx = local_topk(queries[0], docs[2], k=3)
    assert list(idx) == list(np.argsort(-(docs[2] @ queries[0]))[:3])


def test_embedding_cache_roundtrip_and_invalidation(tmp_path):
    vecs = np.eye(3, dtype=np.float32)
    h = texts_hash("m", ["a", "b", "c"])
    path = tmp_path / "doc.npz"
    save_cached(path, vecs, h)
    assert np.allclose(load_cached(path, h), vecs)
    assert load_cached(path, texts_hash("m", ["a", "b", "changed"])) is None


def test_embedder_scrubs_api_key_from_errors():
    embedder = OpenAIEmbedder.__new__(OpenAIEmbedder)
    embedder._secret = "sk-test-SECRET123456"
    message = embedder._scrub("Incorrect API key provided: sk-test-SECRET123456 and sk-abcd****wxyz")
    assert "SECRET123456" not in message
    assert "abcd" not in message
    embedder._secret = "MppcXhfBqwertyuiopasdfghjklzxcvbnmhz4A"
    echoed = embedder._scrub(
        "{'message': 'Incorrect API key provided: MppcXhfB**************hz4A. You can find your API key'}"
    )
    assert "MppcXhfB" not in echoed and "hz4A" not in echoed
    assert "***" in echoed


def test_recall_request_validates_overlap():
    with pytest.raises(ValidationError):
        RecallRequest(chunk_size=200, chunk_overlap=150)
    assert RecallRequest().estimate_only is True
