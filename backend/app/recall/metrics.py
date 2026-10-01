"""Page-level retrieval metrics over chunk rankings."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

KS: Tuple[int, ...] = (1, 3, 5, 10)
TOP_K = max(KS)
# Chunks fetched per query; several chunks share a page, so this must exceed TOP_K
# for the ranking to still hold TOP_K distinct pages after deduplication.
CANDIDATE_CHUNKS = 50

PageRef = Tuple[str, int]


def unique_pages(ranked: Iterable[PageRef]) -> List[PageRef]:
    """Keep each (doc, page) at the rank of its best chunk."""
    seen: Set[PageRef] = set()
    out: List[PageRef] = []
    for ref in ranked:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def global_topk(
    queries: np.ndarray, docs: Iterable[Tuple[int, np.ndarray]], k: int = TOP_K
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Streaming top-k over all documents: returns (scores, doc_idx, chunk_idx), best first."""
    nq = queries.shape[0]
    best_s = np.full((nq, k), -np.inf, dtype=np.float32)
    best_d = np.full((nq, k), -1, dtype=np.int64)
    best_c = np.full((nq, k), -1, dtype=np.int64)
    for doc_idx, vectors in docs:
        if len(vectors) == 0:
            continue
        sims = queries @ vectors.T
        kk = min(k, sims.shape[1])
        idx = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        cand_s = np.concatenate([best_s, np.take_along_axis(sims, idx, 1)], axis=1)
        cand_d = np.concatenate([best_d, np.full(idx.shape, doc_idx, dtype=np.int64)], axis=1)
        cand_c = np.concatenate([best_c, idx], axis=1)
        order = np.argsort(-cand_s, axis=1, kind="stable")[:, :k]
        best_s = np.take_along_axis(cand_s, order, 1)
        best_d = np.take_along_axis(cand_d, order, 1)
        best_c = np.take_along_axis(cand_c, order, 1)
    return best_s, best_d, best_c


def local_topk(query: np.ndarray, vectors: np.ndarray, k: int = TOP_K) -> Tuple[np.ndarray, np.ndarray]:
    """Top-k chunks within one document: returns (scores, chunk_idx), best first."""
    if len(vectors) == 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.int64)
    sims = vectors @ query
    order = np.argsort(-sims, kind="stable")[:k]
    return sims[order], order


def score_ranking(ranked_chunks: Sequence[PageRef], evidence: Set[PageRef]) -> Dict[str, object]:
    """Page-level Hit/Recall helpers after collapsing chunks to distinct pages.

    Hit@k (via first_rank): at least one evidence page appears in the top k pages.
    Recall@k: |evidence ∩ top-k pages| / |evidence| for that question.
    """
    ranked_pages = unique_pages(ranked_chunks)[:TOP_K]
    first_rank: Optional[int] = None
    for rank, ref in enumerate(ranked_pages, start=1):
        if ref in evidence:
            first_rank = rank
            break
    recall_at: Dict[int, float] = {}
    for k in KS:
        found = evidence & set(ranked_pages[:k])
        recall_at[k] = (len(found) / len(evidence)) if evidence else 0.0
    return {"first_rank": first_rank, "recall_at": recall_at}


def _recall_value(row: Dict[str, object], k: int) -> float:
    """Read recall@k whether keys were stored as int (in-memory) or str (JSON)."""
    raw = row.get("recall_at") or {}
    if not isinstance(raw, dict):
        return 0.0
    value = raw.get(k, raw.get(str(k), 0.0))
    return float(value or 0.0)


def summarize(rows: List[Dict[str, object]]) -> Dict[str, object]:
    """rows: dicts with first_rank, recall_at, question_type."""

    def block(items: List[Dict[str, object]]) -> Dict[str, object]:
        n = len(items)
        if n == 0:
            return {"n": 0, "hit_at": {}, "recall_at": {}, "mrr": 0.0}
        hit_at = {
            str(k): sum(1 for r in items if r["first_rank"] and r["first_rank"] <= k) / n
            for k in KS
        }
        # Macro-average of per-question Recall@k (handles multi-page evidence).
        recall_at = {str(k): sum(_recall_value(r, k) for r in items) / n for k in KS}
        mrr = sum(1.0 / r["first_rank"] for r in items if r["first_rank"]) / n
        return {"n": n, "hit_at": hit_at, "recall_at": recall_at, "mrr": mrr}

    by_type: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_type[str(row["question_type"])].append(row)
    return {
        "overall": block(rows),
        "by_type": {t: block(items) for t, items in sorted(by_type.items())},
    }
