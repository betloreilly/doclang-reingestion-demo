"""Recall comparison job: FinanceBench questions over DocLang vs Unstructured chunks."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field, model_validator
from pypdf import PdfReader

from ..cache import ArtifactCache
from ..config import BACKEND_ROOT, Settings, get_settings
from ..doclang_loader import load_doclang_archive
from ..minio_service import MinioService
from ..schemas import JobStatus
from .dataset import FINANCEBENCH_REPO, Question, ensure_dataset, load_questions
from .embed import OpenAIEmbedder, TokenCounter, load_cached, save_cached, texts_hash
from .extract import (
    PageMarkdown,
    RecallChunk,
    align_to_pdf_pages,
    doclang_pages,
    evidence_coverage,
    page_chunks,
    unstructured_pages,
)
from .metrics import (
    CANDIDATE_CHUNKS,
    KS,
    global_topk,
    local_topk,
    score_ranking,
    summarize,
)

PIPELINES = ("doclang", "unstructured")
PIPELINE_LABELS = {
    "doclang": "DocLang (prepared .dclx)",
    "unstructured": "Unstructured OSS (fast)",
}
UNSTRUCTURED_SCRIPT = BACKEND_ROOT / "scripts" / "unstructured_partition.py"
_SAFE_DOC_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_SECRET_ENV_PREFIXES = ("MINIO_", "OPENAI_", "AWS_")
SNIPPET_CHARS = 280


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecallRequest(BaseModel):
    estimate_only: bool = True
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    max_documents: Optional[int] = Field(default=None, ge=1, le=84)

    @model_validator(mode="after")
    def _overlap_below_half_chunk(self) -> "RecallRequest":
        if self.chunk_overlap > self.chunk_size // 2:
            raise ValueError("chunk_overlap must be at most half of chunk_size")
        return self


class RecallJob(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    stage: str = "queued"
    progress: float = 0.0
    message: str = ""
    logs: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    run_id: Optional[str] = None


class RecallManager:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.recall_dir
        self.runs_dir = self.root / "runs"
        self._jobs: Dict[str, RecallJob] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        dataset = self.root / "financebench" / "financebench_open_source.jsonl"
        from .opensearch_store import connection_status as os_status

        model = self.settings.recall_embedding_model
        # Default chunk layout used by the UI; status is advisory for the common case.
        emb_root = (
            self.root
            / "embeddings"
            / re.sub(r"[^A-Za-z0-9_.\-]", "_", model)
            / "1000-150"
        )
        emb_docs = {
            p: len(list((emb_root / p).glob("*.npz"))) if (emb_root / p).is_dir() else 0
            for p in PIPELINES
        }
        queries_cached = (emb_root.parent / "queries.npz").is_file()
        embeddings_ready = (
            emb_docs["doclang"] > 0
            and emb_docs["doclang"] == emb_docs["unstructured"]
            and queries_cached
        )
        os_info = os_status(self.settings)
        os_ready = bool(
            os_info.get("connected")
            and (os_info.get("indexes") or {}).get("doclang", {}).get("docs", 0) > 0
            and (os_info.get("indexes") or {}).get("unstructured", {}).get("docs", 0) > 0
        )

        return {
            "dataset_present": dataset.is_file(),
            "dataset_repo": FINANCEBENCH_REPO,
            "openai_configured": self.settings.openai_configured(),
            "embedding_model": model,
            "embedding_price_per_million": self.settings.recall_embedding_price_per_million,
            "unstructured_available": self.settings.unstructured_python.is_file(),
            "unstructured_cached_documents": len(
                list((self.root / "unstructured" / "fast").glob("*.json"))
            ),
            "embeddings_cached": {
                "doclang_documents": emb_docs["doclang"],
                "unstructured_documents": emb_docs["unstructured"],
                "queries": queries_cached,
                "ready": embeddings_ready,
            },
            "opensearch_ready": os_ready,
            "running": self._lock.locked(),
            "opensearch": os_info,
        }

    def list_runs(self) -> List[Dict[str, Any]]:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        runs = []
        for path in sorted(self.runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
            data.pop("questions", None)
            runs.append(data)
        return runs

    def run_path(self, run_id: str) -> Path:
        uuid.UUID(run_id)
        return self.runs_dir / f"{run_id}.json"

    def get_job(self, job_id: str) -> RecallJob:
        return self._jobs[job_id]

    def start(self, req: RecallRequest, bench_busy: bool = False) -> RecallJob:
        if bench_busy:
            raise RuntimeError("A cost benchmark is running. Start the recall run after it finishes.")
        if self._lock.locked():
            raise RuntimeError("A recall comparison is already running.")
        if not req.estimate_only and not self.settings.openai_configured():
            # Cached embeddings are enough for a scored run; the API key is only
            # required when something still needs to be embedded.
            emb = self.status().get("embeddings_cached") or {}
            if not emb.get("ready"):
                raise RuntimeError(
                    "OPENAI_API_KEY is not set in backend/.env, and embeddings are not "
                    "fully cached. Run an estimate, add the key, or use Evaluate via "
                    "OpenSearch if indexes are already loaded."
                )
        job = RecallJob(job_id=str(uuid.uuid4()), message="Recall comparison queued")
        self._jobs[job.job_id] = job
        threading.Thread(target=self._run, args=(job, req), daemon=True).start()
        return job

    # ---------------------------------------------------------------- helpers
    def _log(self, job: RecallJob, message: str) -> None:
        job.logs.append(f"[{_now()}] {message}")
        job.message = message

    def _progress(self, job: RecallJob, stage: str, value: float) -> None:
        job.stage = stage
        job.progress = round(min(max(value, 0.0), 1.0), 4)

    def _resolve_files(
        self, doc_names: List[str], job: RecallJob, on_progress: Callable[[float], None]
    ) -> Tuple[Dict[str, Dict[str, Path]], List[str]]:
        """Find each doc's .dclx and .pdf locally, downloading missing ones read-only."""
        wanted = {".dclx", ".pdf"}
        local: Dict[Tuple[str, str], Path] = {}
        for root in (self.settings.local_docs_dir, self.settings.cache_dir):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in wanted and path.stat().st_size > 0:
                    local.setdefault((path.stem, path.suffix.lower()), path)

        missing = [(d, s) for d in doc_names for s in wanted if (d, s) not in local]
        errors: List[str] = []
        if missing:
            minio = MinioService(self.settings)
            if not self.settings.minio_configured():
                errors.append(
                    f"{len(missing)} FinanceBench files are not cached and MinIO is not configured."
                )
            else:
                cache = ArtifactCache(self.settings)
                remote: Dict[Tuple[str, str], str] = {}
                for obj in minio.list_objects():
                    p = PurePosixPath(obj.key)
                    if p.suffix.lower() in wanted:
                        remote.setdefault((p.stem, p.suffix.lower()), obj.key)
                for i, ref in enumerate(missing):
                    key = remote.get(ref)
                    if key is None:
                        errors.append(f"{ref[0]}{ref[1]} not found in the bucket.")
                        continue
                    dest = cache.ensure_parent(key)
                    self._log(job, f"Downloading {ref[0]}{ref[1]}")
                    minio.download_object(key, dest)
                    local[ref] = dest
                    on_progress((i + 1) / len(missing))

        files: Dict[str, Dict[str, Path]] = {}
        for doc in doc_names:
            dclx, pdf = local.get((doc, ".dclx")), local.get((doc, ".pdf"))
            if dclx and pdf:
                files[doc] = {"dclx": dclx, "pdf": pdf}
        return files, errors

    def _run_unstructured(
        self, pdfs: Dict[str, Path], job: RecallJob, on_progress: Callable[[float], None]
    ) -> Tuple[Dict[str, float], List[str]]:
        out_dir = self.root / "unstructured" / "fast"
        todo = [(doc, pdf) for doc, pdf in pdfs.items() if not (out_dir / f"{doc}.json").is_file()]
        if not todo:
            return {}, []
        python = self.settings.unstructured_python
        if not python.is_file():
            raise RuntimeError(
                f"Unstructured interpreter not found at {python}. Create backend/.venv-unstructured "
                "(see README, 'Recall comparison') or set UNSTRUCTURED_PYTHON in backend/.env."
            )
        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        workers = max(1, min(4, (os.cpu_count() or 2) // 2, len(todo)))
        groups = [todo[i::workers] for i in range(workers)]
        seconds: Dict[str, float] = {}
        errors: List[str] = []
        done = [0]
        lock = threading.Lock()
        stderr_path = out_dir / "_stderr.log"
        out_dir.mkdir(parents=True, exist_ok=True)
        self._log(job, f"Partitioning {len(todo)} PDFs with Unstructured (fast), {workers} workers")

        def consume(proc: subprocess.Popen) -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    status = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = Path(status.get("pdf", "")).stem
                with lock:
                    done[0] += 1
                    if status.get("ok"):
                        seconds[name] = float(status.get("seconds", 0.0))
                        self._log(job, f"Unstructured {name}: {status.get('elements')} elements in {status.get('seconds')}s")
                    else:
                        errors.append(f"Unstructured failed on {name}: {status.get('error')}")
                    on_progress(done[0] / len(todo))

        with stderr_path.open("a") as stderr:
            procs, readers = [], []
            for group in groups:
                proc = subprocess.Popen(
                    [str(python), str(UNSTRUCTURED_SCRIPT)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    text=True,
                    env=env,
                    cwd=str(BACKEND_ROOT),
                )
                assert proc.stdin is not None
                proc.stdin.write(
                    json.dumps([{"pdf": str(pdf), "out": str(out_dir / f"{doc}.json")} for doc, pdf in group])
                )
                proc.stdin.close()
                reader = threading.Thread(target=consume, args=(proc,), daemon=True)
                reader.start()
                procs.append(proc)
                readers.append(reader)
            for proc, reader in zip(procs, readers):
                proc.wait()
                reader.join()
        return seconds, errors

    # -------------------------------------------------------------------- run
    def _run(self, job: RecallJob, req: RecallRequest) -> None:
        with self._lock:
            job.status = JobStatus.running
            try:
                self._execute(job, req)
                job.status = JobStatus.completed
                self._progress(job, "done", 1.0)
            except Exception as exc:  # noqa: BLE001
                job.status = JobStatus.failed
                job.error = str(exc)
                self._log(job, f"FAILED: {exc}")

    def _execute(self, job: RecallJob, req: RecallRequest) -> None:
        s = self.settings
        started_at = _now()
        errors: List[str] = []

        self._progress(job, "dataset", 0.01)
        questions = load_questions(ensure_dataset(self.root / "financebench"))
        doc_names = sorted({q.doc_name for q in questions} | {e.doc_name for q in questions for e in q.evidence})
        unsafe = [d for d in doc_names if not _SAFE_DOC_RE.match(d)]
        if unsafe:
            raise RuntimeError(f"Unexpected FinanceBench document names: {unsafe[:3]}")
        if req.max_documents:
            doc_names = sorted({q.doc_name for q in questions})[: req.max_documents]
        self._log(job, f"FinanceBench: {len(questions)} questions, {len(doc_names)} documents in scope")

        files, resolve_errors = self._resolve_files(
            doc_names, job, lambda f: self._progress(job, "download", 0.02 + 0.08 * f)
        )
        errors.extend(resolve_errors)

        # DocLang: load prepared archives only.
        pages: Dict[str, Dict[str, PageMarkdown]] = {p: {} for p in PIPELINES}
        extract_seconds: Dict[str, Dict[str, float]] = {p: {} for p in PIPELINES}
        artifacts_root = s.artifacts_dir / "recall"
        realigned: List[Dict[str, Any]] = []
        for i, doc in enumerate(sorted(files)):
            self._progress(job, "doclang", 0.10 + 0.10 * i / max(len(files), 1))
            document, elapsed, _ = load_doclang_archive(files[doc]["dclx"], artifacts_root / doc)
            doc_pages = doclang_pages(document)
            extract_seconds["doclang"][doc] = elapsed
            # FinanceBench labels PDF pages; realign archives that dropped pages.
            reader = PdfReader(str(files[doc]["pdf"]))
            if len(document.pages) != len(reader.pages):
                doc_pages, shifted = align_to_pdf_pages(
                    doc_pages, [page.extract_text() or "" for page in reader.pages]
                )
                realigned.append(
                    {
                        "doc_name": doc,
                        "doclang_pages": len(document.pages),
                        "pdf_pages": len(reader.pages),
                        "pages_renumbered": shifted,
                    }
                )
                self._log(
                    job,
                    f"{doc}: DocLang has {len(document.pages)} pages, PDF has {len(reader.pages)}; "
                    f"renumbered {shifted} pages to PDF page numbers",
                )
            pages["doclang"][doc] = doc_pages

        unstructured_seconds, unstructured_errors = self._run_unstructured(
            {doc: f["pdf"] for doc, f in files.items()},
            job,
            lambda f: self._progress(job, "unstructured", 0.20 + 0.40 * f),
        )
        errors.extend(unstructured_errors)
        extract_seconds["unstructured"].update(unstructured_seconds)
        for doc in files:
            path = self.root / "unstructured" / "fast" / f"{doc}.json"
            if path.is_file():
                pages["unstructured"][doc] = unstructured_pages(json.loads(path.read_text()))

        # Only documents both pipelines produced are compared, so the question set is identical.
        docs = sorted(set(pages["doclang"]) & set(pages["unstructured"]))
        excluded = sorted(set(doc_names) - set(docs))
        if excluded:
            errors.append(f"Excluded {len(excluded)} documents missing from one pipeline: {excluded[:5]}")
        doc_set = set(docs)
        in_scope = [q for q in questions if q.doc_name in doc_set and q.evidence]
        if not in_scope:
            raise RuntimeError("No FinanceBench questions left to evaluate.")

        self._progress(job, "chunk", 0.60)
        chunks: Dict[str, Dict[str, List[RecallChunk]]] = {
            p: {doc: page_chunks(pages[p][doc], doc, req.chunk_size, req.chunk_overlap) for doc in docs}
            for p in PIPELINES
        }

        coverage = self._coverage(in_scope, pages)

        counter = TokenCounter()
        model = s.recall_embedding_model
        cfg = f"{req.chunk_size}-{req.chunk_overlap}"
        emb_root = self.root / "embeddings" / re.sub(r"[^A-Za-z0-9_.\-]", "_", model) / cfg
        token_stats: Dict[str, Dict[str, int]] = {}
        pending: List[Tuple[str, str, List[str], str, Path]] = []
        for p in PIPELINES:
            total = uncached = 0
            for doc in docs:
                texts = [c.text for c in chunks[p][doc]]
                tokens = sum(counter.count(t) for t in texts)
                total += tokens
                h = texts_hash(model, texts)
                path = emb_root / p / f"{doc}.npz"
                if load_cached(path, h) is None:
                    uncached += tokens
                    pending.append((p, doc, texts, h, path))
            token_stats[p] = {"total": total, "uncached": uncached}
        query_texts = [q.question for q in in_scope]
        query_tokens = sum(counter.count(t) for t in query_texts)
        query_hash = texts_hash(model, query_texts)
        query_path = emb_root.parent / "queries.npz"
        query_cached = load_cached(query_path, query_hash) is not None

        price = s.recall_embedding_price_per_million
        tokens_total = sum(v["total"] for v in token_stats.values()) + query_tokens
        tokens_uncached = sum(v["uncached"] for v in token_stats.values()) + (0 if query_cached else query_tokens)
        embedding_info: Dict[str, Any] = {
            "model": model,
            "price_per_million": price,
            "tokens_total": tokens_total,
            "tokens_uncached": tokens_uncached,
            "estimated_cost_full_usd": tokens_total / 1e6 * price,
            "estimated_cost_remaining_usd": tokens_uncached / 1e6 * price,
            "billed_tokens_this_run": 0,
            "cost_this_run_usd": 0.0,
        }
        self._log(
            job,
            f"Embedding tokens: {tokens_total:,} total, {tokens_uncached:,} not cached "
            f"(≈ ${tokens_uncached / 1e6 * price:.2f} at ${price}/1M)",
        )

        retrieval: Dict[str, Dict[str, Any]] = {}
        per_question: Dict[str, Dict[str, Dict[str, Any]]] = {p: {} for p in PIPELINES}
        if not req.estimate_only:
            if pending or not query_cached:
                if not s.openai_configured():
                    raise RuntimeError(
                        "Some embeddings are missing from the local cache and OPENAI_API_KEY "
                        "is not set. Add the key, or restore the cache under data/recall/embeddings/."
                    )
                embedder = OpenAIEmbedder(
                    s.openai_api_key.get_secret_value(), model, s.openai_base_url
                )
                embedded = [0]
                pending_inputs = sum(len(t) for _, _, t, _, _ in pending) + (
                    0 if query_cached else len(query_texts)
                )

                def on_batch(n: int) -> None:
                    embedded[0] += n
                    self._progress(
                        job, "embed", 0.62 + 0.30 * embedded[0] / max(pending_inputs, 1)
                    )

                for p, doc, texts, h, path in pending:
                    self._log(
                        job, f"Embedding {PIPELINE_LABELS[p]} · {doc} ({len(texts)} chunks)"
                    )
                    vectors = (
                        embedder.embed(texts, on_batch)
                        if texts
                        else np.zeros((0, 1), dtype=np.float32)
                    )
                    save_cached(path, vectors, h)
                if not query_cached:
                    save_cached(
                        query_path, embedder.embed(query_texts, on_batch), query_hash
                    )
                embedding_info["billed_tokens_this_run"] = tokens_uncached
                embedding_info["cost_this_run_usd"] = tokens_uncached / 1e6 * price
            else:
                self._log(
                    job,
                    "All embeddings already cached on disk — skipping OpenAI calls.",
                )
                self._progress(job, "embed", 0.9)

            self._progress(job, "retrieve", 0.93)
            queries = load_cached(query_path, query_hash)
            for p in PIPELINES:
                retrieval[p], per_question[p] = self._retrieve(
                    in_scope, docs, chunks[p], queries, emb_root / p, model
                )

        pipelines_out: Dict[str, Any] = {}
        for p in PIPELINES:
            pipelines_out[p] = {
                "label": PIPELINE_LABELS[p],
                "chunks": sum(len(chunks[p][d]) for d in docs),
                "tokens": token_stats[p]["total"],
                "pages_with_text": sum(len(pages[p][d]) for d in docs),
                "extraction_seconds_measured": round(sum(extract_seconds[p].values()), 2),
                "evidence_coverage": coverage[p]["summary"],
                "retrieval": retrieval.get(p),
            }

        question_rows = []
        for q in in_scope:
            row: Dict[str, Any] = {
                "financebench_id": q.financebench_id,
                "doc_name": q.doc_name,
                "question_type": q.question_type,
                "question": q.question,
                "answer": q.answer,
                "evidence_pages": sorted({e.page for e in q.evidence}),
            }
            for p in PIPELINES:
                row[p] = {"evidence_coverage": coverage[p]["by_question"].get(q.financebench_id)}
                row[p].update(per_question[p].get(q.financebench_id, {}))
            question_rows.append(row)

        run_id = str(uuid.uuid4())
        result = {
            "run_id": run_id,
            "created_at": started_at,
            "finished_at": _now(),
            "estimate_only": req.estimate_only,
            "settings": {
                "chunk_size": req.chunk_size,
                "chunk_overlap": req.chunk_overlap,
                "max_documents": req.max_documents,
                "unstructured_strategy": "fast",
                "ks": list(KS),
                "retrieval_unit": "distinct pages (best chunk per page)",
            },
            "dataset": {
                "name": "FinanceBench (open source)",
                "repo": FINANCEBENCH_REPO,
                "questions_total": len(questions),
                "questions_evaluated": len(in_scope),
                "documents_evaluated": len(docs),
            },
            "embedding": embedding_info,
            "pipelines": pipelines_out,
            "doclang_page_realignment": realigned,
            "excluded_documents": excluded,
            "errors": list(dict.fromkeys(errors)),
            "partial": bool(excluded or errors),
            "questions": question_rows,
        }
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.runs_dir / f"{run_id}.json").write_text(json.dumps(result, indent=2))
        job.run_id = run_id
        self._log(job, "Recall comparison complete." if not req.estimate_only else "Estimate complete.")

    def _coverage(
        self, questions: List[Question], pages: Dict[str, Dict[str, PageMarkdown]]
    ) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for p in PIPELINES:
            by_question: Dict[str, float] = {}
            scores: List[float] = []
            for q in questions:
                q_scores = [
                    evidence_coverage(e.text, pages[p].get(e.doc_name, {}).get(e.page, ""))
                    for e in q.evidence
                ]
                by_question[q.financebench_id] = sum(q_scores) / len(q_scores)
                scores.extend(q_scores)
            n = max(len(scores), 1)
            out[p] = {
                "by_question": by_question,
                "summary": {
                    "evidence_items": len(scores),
                    "mean": sum(scores) / n,
                    "share_at_least_90pct": sum(1 for v in scores if v >= 0.9) / n,
                },
            }
        return out

    def _retrieve(
        self,
        questions: List[Question],
        docs: List[str],
        chunks: Dict[str, List[RecallChunk]],
        queries,
        emb_dir: Path,
        model: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        by_doc: Dict[str, List[int]] = {}
        for qi, q in enumerate(questions):
            by_doc.setdefault(q.doc_name, []).append(qi)
        local_hits: Dict[int, List[Tuple[str, int, float]]] = {}

        def stream():
            for di, doc in enumerate(docs):
                texts = [c.text for c in chunks[doc]]
                vectors = load_cached(emb_dir / f"{doc}.npz", texts_hash(model, texts))
                if vectors is None:
                    raise RuntimeError(f"Missing embeddings for {doc}; rerun the comparison.")
                for qi in by_doc.get(doc, []):
                    scores, idx = local_topk(queries[qi], vectors, CANDIDATE_CHUNKS)
                    local_hits[qi] = [(doc, int(i), float(sc)) for sc, i in zip(scores, idx)]
                yield di, vectors

        g_scores, g_docs, g_chunks = global_topk(queries, stream(), CANDIDATE_CHUNKS)

        def describe(doc: str, ci: int, score: float, evidence: set) -> Dict[str, Any]:
            chunk = chunks[doc][ci]
            return {
                "doc_name": doc,
                "page": chunk.page,
                "score": round(score, 4),
                "relevant": (doc, chunk.page) in evidence,
                "snippet": chunk.text[:SNIPPET_CHARS],
            }

        def top_pages(hits: List[Tuple[str, int, float]], evidence: set, n: int = 3):
            out, seen = [], set()
            for d, c, sc in hits:
                ref = (d, chunks[d][c].page)
                if ref in seen:
                    continue
                seen.add(ref)
                out.append(describe(d, c, sc, evidence))
                if len(out) == n:
                    break
            return out

        rows = {"global": [], "per_document": []}
        details: Dict[str, Dict[str, Any]] = {}
        for qi, q in enumerate(questions):
            evidence = q.evidence_pages()
            g = [
                (docs[int(d)], int(c), float(sc))
                for sc, d, c in zip(g_scores[qi], g_docs[qi], g_chunks[qi])
                if d >= 0
            ]
            l_ = local_hits.get(qi, [])
            g_score = score_ranking([(d, chunks[d][c].page) for d, c, _ in g], evidence)
            l_score = score_ranking([(d, chunks[d][c].page) for d, c, _ in l_], evidence)
            rows["global"].append({**g_score, "question_type": q.question_type})
            rows["per_document"].append({**l_score, "question_type": q.question_type})
            details[q.financebench_id] = {
                "global_first_rank": g_score["first_rank"],
                "per_document_first_rank": l_score["first_rank"],
                "global_top": top_pages(g, evidence),
                "per_document_top": top_pages(l_, evidence),
            }
        return {"global": summarize(rows["global"]), "per_document": summarize(rows["per_document"])}, details


_manager: Optional[RecallManager] = None


def get_recall_manager() -> RecallManager:
    global _manager
    if _manager is None:
        _manager = RecallManager()
    return _manager
