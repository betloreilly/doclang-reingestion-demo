"""Score FinanceBench questions via OpenSearch k-NN (global + per-document)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config import Settings, get_settings
from .dataset import Question, ensure_dataset, load_questions
from .embed import load_cached, texts_hash
from .metrics import CANDIDATE_CHUNKS, KS, score_ranking, summarize
from .opensearch_store import connection_status, knn_search

PIPELINES = ("doclang", "unstructured")
PIPELINE_LABELS = {
    "doclang": "DocLang (prepared .dclx)",
    "unstructured": "Unstructured OSS (fast)",
}


def _query_vectors(settings: Settings, questions: Sequence[Question]) -> np.ndarray:
    model = settings.recall_embedding_model
    texts = [q.question for q in questions]
    path = (
        settings.recall_dir
        / "embeddings"
        / re.sub(r"[^A-Za-z0-9_.\-]", "_", model)
        / "queries.npz"
    )
    vectors = load_cached(path, texts_hash(model, texts))
    if vectors is None:
        # Fallback: try any queries.npz under the model dir (hash may differ if question set filtered).
        if path.is_file():
            with np.load(path) as data:
                vectors = data["vectors"].astype(np.float32)
                if len(vectors) == len(questions):
                    return vectors
        raise RuntimeError(
            f"Cached query embeddings not found at {path}. Run a full recall comparison first."
        )
    return vectors


def _top_pages(hits: List[Dict[str, Any]], n: int = 3) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for hit in hits:
        ref = (hit["doc_name"], hit["page"])
        if ref in seen:
            continue
        seen.add(ref)
        out.append(hit)
        if len(out) == n:
            break
    return out


def evaluate_opensearch(
    settings: Optional[Settings] = None,
    max_documents: Optional[int] = None,
) -> Dict[str, Any]:
    """Run FinanceBench retrieval against OpenSearch indexes; return a recall-run shaped dict."""
    s = settings or get_settings()
    status = connection_status(s)
    if not status["connected"]:
        raise RuntimeError(
            f"OpenSearch is not reachable: {status.get('error') or s.opensearch_url}"
        )
    for pipeline in PIPELINES:
        info = (status.get("indexes") or {}).get(pipeline) or {}
        if not info.get("exists") or not info.get("docs"):
            raise RuntimeError(
                f"Index for {pipeline} is empty. Run OpenSearch ingest first "
                f"(POST /api/recall/opensearch/ingest)."
            )

    all_questions = load_questions(ensure_dataset(s.recall_dir / "financebench"))
    all_with_evidence = [q for q in all_questions if q.evidence]
    queries = _query_vectors(s, all_with_evidence)

    if max_documents:
        keep = set(sorted({q.doc_name for q in all_with_evidence})[:max_documents])
        pairs = [
            (q, queries[i])
            for i, q in enumerate(all_with_evidence)
            if q.doc_name in keep
        ]
    else:
        pairs = list(zip(all_with_evidence, queries))

    in_scope = [q for q, _ in pairs]
    query_vecs = np.stack([v for _, v in pairs]) if pairs else np.zeros((0, 1))

    pipelines_out: Dict[str, Any] = {}
    question_rows: List[Dict[str, Any]] = [
        {
            "financebench_id": q.financebench_id,
            "doc_name": q.doc_name,
            "question_type": q.question_type,
            "question": q.question,
            "answer": q.answer,
            "evidence_pages": sorted({e.page for e in q.evidence}),
            "doclang": {},
            "unstructured": {},
        }
        for q in in_scope
    ]
    by_id = {row["financebench_id"]: row for row in question_rows}

    for pipeline in PIPELINES:
        global_rows: List[Dict[str, object]] = []
        local_rows: List[Dict[str, object]] = []
        for qi, q in enumerate(in_scope):
            evidence = q.evidence_pages()
            g_hits = knn_search(pipeline, query_vecs[qi], k=CANDIDATE_CHUNKS, settings=s)
            l_hits = knn_search(
                pipeline,
                query_vecs[qi],
                k=CANDIDATE_CHUNKS,
                doc_name=q.doc_name,
                settings=s,
            )
            for hit in g_hits:
                hit["relevant"] = (hit["doc_name"], hit["page"]) in evidence
            for hit in l_hits:
                hit["relevant"] = (hit["doc_name"], hit["page"]) in evidence
            g_score = score_ranking(
                [(h["doc_name"], int(h["page"])) for h in g_hits], evidence
            )
            l_score = score_ranking(
                [(h["doc_name"], int(h["page"])) for h in l_hits], evidence
            )
            global_rows.append({**g_score, "question_type": q.question_type})
            local_rows.append({**l_score, "question_type": q.question_type})
            by_id[q.financebench_id][pipeline] = {
                "global_first_rank": g_score["first_rank"],
                "per_document_first_rank": l_score["first_rank"],
                "global_top": _top_pages(g_hits),
                "per_document_top": _top_pages(l_hits),
            }
        info = status["indexes"][pipeline]
        pipelines_out[pipeline] = {
            "label": PIPELINE_LABELS[pipeline],
            "chunks": info.get("docs", 0),
            "tokens": 0,
            "pages_with_text": 0,
            "extraction_seconds_measured": 0,
            "evidence_coverage": {
                "evidence_items": 0,
                "mean": 0.0,
                "share_at_least_90pct": 0.0,
            },
            "retrieval": {
                "global": summarize(global_rows),
                "per_document": summarize(local_rows),
            },
            "backend": "opensearch",
            "index": info.get("name"),
        }

    return {
        "estimate_only": False,
        "retrieval_backend": "opensearch",
        "settings": {
            "chunk_size": None,
            "chunk_overlap": None,
            "max_documents": max_documents,
            "unstructured_strategy": "fast",
            "ks": list(KS),
            "retrieval_unit": "OpenSearch k-NN, distinct pages (best chunk per page)",
        },
        "dataset": {
            "name": "FinanceBench (open source)",
            "repo": "https://github.com/patronus-ai/financebench",
            "questions_total": len(all_questions),
            "questions_evaluated": len(in_scope),
            "documents_evaluated": len({q.doc_name for q in in_scope}),
        },
        "embedding": {
            "model": s.recall_embedding_model,
            "price_per_million": s.recall_embedding_price_per_million,
            "tokens_total": 0,
            "tokens_uncached": 0,
            "estimated_cost_full_usd": 0.0,
            "estimated_cost_remaining_usd": 0.0,
            "billed_tokens_this_run": 0,
            "cost_this_run_usd": 0.0,
        },
        "pipelines": pipelines_out,
        "opensearch": status,
        "excluded_documents": [],
        "errors": [],
        "partial": False,
        "questions": question_rows,
    }
