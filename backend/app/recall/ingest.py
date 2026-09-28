"""Ingest FinanceBench recall chunks + cached embeddings into OpenSearch."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..doclang_loader import load_doclang_archive
from .embed import load_cached, texts_hash
from .extract import (
    PageMarkdown,
    align_to_pdf_pages,
    doclang_pages,
    page_chunks,
    unstructured_pages,
)
from .opensearch_store import (
    bulk_index_chunks,
    connection_status,
    ensure_index,
    get_client,
    now_iso,
)

PIPELINES = ("doclang", "unstructured")
_SAFE_DOC_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class IngestRequest(BaseModel):
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    # Default false: reuse existing OpenSearch indexes when they already have docs.
    recreate_indexes: bool = False
    max_documents: Optional[int] = Field(default=None, ge=1, le=84)


class IngestJob(BaseModel):
    job_id: str
    status: str = "queued"
    stage: str = "queued"
    progress: float = 0.0
    message: str = ""
    logs: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


def _find_files(settings: Settings, doc_names: List[str]) -> Dict[str, Dict[str, Path]]:
    wanted = {".dclx", ".pdf"}
    local: Dict[Tuple[str, str], Path] = {}
    for root in (settings.local_docs_dir, settings.cache_dir):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in wanted and path.stat().st_size > 0:
                local.setdefault((path.stem, path.suffix.lower()), path)
    files: Dict[str, Dict[str, Path]] = {}
    for doc in doc_names:
        dclx, pdf = local.get((doc, ".dclx")), local.get((doc, ".pdf"))
        if dclx and pdf:
            files[doc] = {"dclx": dclx, "pdf": pdf}
    return files


def _load_pages(
    settings: Settings, files: Dict[str, Dict[str, Path]]
) -> Dict[str, Dict[str, PageMarkdown]]:
    from pypdf import PdfReader

    pages: Dict[str, Dict[str, PageMarkdown]] = {p: {} for p in PIPELINES}
    artifacts = settings.artifacts_dir / "recall"
    for doc, paths in files.items():
        document, _, _ = load_doclang_archive(paths["dclx"], artifacts / doc)
        doc_pages = doclang_pages(document)
        reader = PdfReader(str(paths["pdf"]))
        if len(document.pages) != len(reader.pages):
            doc_pages, _ = align_to_pdf_pages(
                doc_pages, [page.extract_text() or "" for page in reader.pages]
            )
        pages["doclang"][doc] = doc_pages
        u_path = settings.recall_dir / "unstructured" / "fast" / f"{doc}.json"
        if u_path.is_file():
            pages["unstructured"][doc] = unstructured_pages(json.loads(u_path.read_text()))
    return pages


def ingest_pipelines(
    req: IngestRequest,
    settings: Optional[Settings] = None,
    on_progress: Optional[Callable[[str, float, str], None]] = None,
) -> Dict[str, Any]:
    """Rebuild chunks, attach cached embeddings, bulk-index into OpenSearch."""
    s = settings or get_settings()
    status = connection_status(s)
    if not status["connected"]:
        raise RuntimeError(
            f"OpenSearch is not reachable at {s.opensearch_url}. "
            f"Start it with `docker compose up -d` from the repo root. "
            f"Detail: {status.get('error')}"
        )

    model = s.recall_embedding_model
    cfg = f"{req.chunk_size}-{req.chunk_overlap}"
    emb_root = (
        s.recall_dir
        / "embeddings"
        / re.sub(r"[^A-Za-z0-9_.\-]", "_", model)
        / cfg
    )
    if not emb_root.is_dir():
        raise RuntimeError(
            f"No cached embeddings under {emb_root}. Run the recall comparison first "
            "(non-estimate) so vectors exist on disk."
        )

    emb_docs = {
        p: sorted(path.stem for path in (emb_root / p).glob("*.npz"))
        for p in PIPELINES
    }
    docs = sorted(set(emb_docs["doclang"]) & set(emb_docs["unstructured"]))
    if req.max_documents:
        docs = docs[: req.max_documents]
    unsafe = [d for d in docs if not _SAFE_DOC_RE.match(d)]
    if unsafe:
        raise RuntimeError(f"Unexpected document names: {unsafe[:3]}")
    if not docs:
        raise RuntimeError("No documents with embeddings for both pipelines.")

    summary: Dict[str, Any] = {
        "embedding_model": model,
        "chunk_size": req.chunk_size,
        "chunk_overlap": req.chunk_overlap,
        "vector_dim": s.recall_vector_dim,
        "documents": len(docs),
        "pipelines": {},
        "opensearch": {"url": s.opensearch_url, "indexes": {}},
        "used_cached_embeddings": True,
        "skipped_all": False,
    }

    # Fast path: indexes already populated — no OpenAI, no re-chunk, no bulk.
    if not req.recreate_indexes:
        client = get_client(s)
        already: Dict[str, int] = {}
        for pipeline in PIPELINES:
            name = s.opensearch_index(pipeline)
            if client.indices.exists(index=name):
                already[pipeline] = int(client.count(index=name)["count"])
            else:
                already[pipeline] = 0
        if already["doclang"] > 0 and already["unstructured"] > 0:
            if on_progress:
                on_progress(
                    "skip",
                    1.0,
                    "OpenSearch already has both indexes — skipping ingest "
                    "(no re-embedding). Pass recreate_indexes to rebuild.",
                )
            for pipeline in PIPELINES:
                name = s.opensearch_index(pipeline)
                summary["pipelines"][pipeline] = {
                    "index": name,
                    "chunks": already[pipeline],
                    "errors": 0,
                    "skipped_documents": 0,
                    "skipped_ingest": True,
                }
                summary["opensearch"]["indexes"][pipeline] = name
            summary["skipped_all"] = True
            return summary

    if on_progress:
        on_progress("pages", 0.05, f"Loading pages for {len(docs)} documents")
    files = _find_files(s, docs)
    missing = [d for d in docs if d not in files]
    if missing:
        raise RuntimeError(f"Missing cached .dclx/.pdf for: {missing[:5]}")
    pages = _load_pages(s, {d: files[d] for d in docs})
    docs = sorted(set(pages["doclang"]) & set(pages["unstructured"]) & set(docs))
    summary["documents"] = len(docs)

    for pi, pipeline in enumerate(PIPELINES):
        index_name = s.opensearch_index(pipeline)
        client = get_client(s)
        exists = client.indices.exists(index=index_name)
        existing_docs = int(client.count(index=index_name)["count"]) if exists else 0

        if exists and existing_docs > 0 and not req.recreate_indexes:
            if on_progress:
                on_progress(
                    "skip",
                    0.2 + 0.4 * pi,
                    f"Skipping {pipeline}: {index_name} already has {existing_docs:,} chunks "
                    "(pass recreate_indexes to rebuild)",
                )
            summary["pipelines"][pipeline] = {
                "index": index_name,
                "chunks": existing_docs,
                "errors": 0,
                "skipped_documents": 0,
                "skipped_ingest": True,
            }
            summary["opensearch"]["indexes"][pipeline] = index_name
            continue

        if on_progress:
            on_progress(
                "index",
                0.1 + 0.4 * pi,
                f"{'Recreating' if req.recreate_indexes else 'Creating'} index for {pipeline}",
            )
        index_name = ensure_index(
            pipeline, s.recall_vector_dim, s, recreate=req.recreate_indexes or exists
        )
        chunks = {
            doc: page_chunks(
                pages[pipeline][doc], doc, req.chunk_size, req.chunk_overlap
            )
            for doc in docs
        }
        actions: List[Dict[str, Any]] = []
        skipped = 0
        for di, doc in enumerate(docs):
            texts = [c.text for c in chunks[doc]]
            path = emb_root / pipeline / f"{doc}.npz"
            vectors = load_cached(path, texts_hash(model, texts))
            if vectors is None:
                skipped += 1
                continue
            if len(vectors) != len(chunks[doc]):
                raise RuntimeError(
                    f"{pipeline}/{doc}: {len(vectors)} vectors vs {len(chunks[doc])} chunks. "
                    "Re-run recall with these chunk settings."
                )
            for i, (chunk, vec) in enumerate(zip(chunks[doc], vectors)):
                actions.append(
                    {
                        "pipeline": pipeline,
                        "doc_name": doc,
                        "chunk_id": f"{pipeline}::{doc}::{i:05d}",
                        "page": chunk.page,
                        "chunk_index": i,
                        "text": chunk.text,
                        "embedding_model": model,
                        "chunk_size": req.chunk_size,
                        "chunk_overlap": req.chunk_overlap,
                        "ingested_at": now_iso(),
                        "embedding": vec.astype(np.float32).tolist(),
                    }
                )
            if on_progress and di % 5 == 0:
                on_progress(
                    "bulk",
                    0.15 + 0.4 * pi + 0.35 * (di + 1) / max(len(docs), 1),
                    f"Preparing {pipeline} · {doc}",
                )
        if on_progress:
            on_progress(
                "bulk", 0.55 + 0.4 * pi, f"Bulk indexing {pipeline} ({len(actions)} chunks)"
            )
        ok, err = bulk_index_chunks(pipeline, actions, s)
        summary["pipelines"][pipeline] = {
            "index": index_name,
            "chunks": ok,
            "errors": err,
            "skipped_documents": skipped,
            "skipped_ingest": False,
        }
        summary["opensearch"]["indexes"][pipeline] = index_name

    if on_progress:
        on_progress("done", 1.0, "OpenSearch ingest complete")
    return summary


_ingest_jobs: Dict[str, IngestJob] = {}
_ingest_lock = threading.Lock()


def start_ingest(req: IngestRequest) -> IngestJob:
    import uuid

    if _ingest_lock.locked():
        raise RuntimeError("An OpenSearch ingest job is already running.")
    job = IngestJob(job_id=str(uuid.uuid4()), message="Ingest queued")
    _ingest_jobs[job.job_id] = job

    def run() -> None:
        with _ingest_lock:
            job.status = "running"

            def progress(stage: str, value: float, message: str) -> None:
                job.stage = stage
                job.progress = round(value, 4)
                job.message = message
                job.logs.append(message)

            try:
                job.result = ingest_pipelines(req, on_progress=progress)
                job.status = "completed"
                job.stage = "done"
                job.progress = 1.0
                job.message = "OpenSearch ingest complete"
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = str(exc)
                job.message = f"FAILED: {exc}"
                job.logs.append(job.message)

    threading.Thread(target=run, daemon=True).start()
    return job


def get_ingest_job(job_id: str) -> IngestJob:
    return _ingest_jobs[job_id]
