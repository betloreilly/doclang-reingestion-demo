"""Background job runner with exclusive benchmark lock."""

from __future__ import annotations

import json
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cache import ArtifactCache
from .chunking import chunk_document
from .config import Settings, get_settings
from .cost import compute_costs, compute_time_savings
from .doclang_loader import DocLangLoadError, load_doclang_archive
from .embeddings import EmbeddingService
from .local_source import resolve_local_path, scan_local_objects
from .minio_service import MinioService
from .pages import count_pdf_pages, pages_from_doclang_document
from .pairing import apply_manual_pairing, index_objects, pair_objects
from .schemas import (
    ChunkInspection,
    DocumentPair,
    DocumentSource,
    DownloadRequest,
    JobStatus,
    JobSummary,
    ProcessRequest,
    ProcessSettings,
    RunMode,
    RunResult,
    StageTiming,
)
from .tokens import LocalTokenizer, count_chunks


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.minio = MinioService(self.settings)
        self.cache = ArtifactCache(self.settings)
        self._jobs: Dict[str, JobSummary] = {}
        self._pairs_cache: List[DocumentPair] = []
        self._objects_by_key: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._bench_lock = threading.Lock()
        self._local_tokenizer = LocalTokenizer(self.settings.embedding_model)
        self._source: DocumentSource = DocumentSource.minio

    def local_roots(self) -> List[Path]:
        return [self.settings.cache_dir, self.settings.local_docs_dir]

    # ------------------------------------------------------------------ listing
    def refresh_pairs(self, source: DocumentSource = DocumentSource.minio) -> List[DocumentPair]:
        self._source = source
        if source == DocumentSource.local:
            objects = scan_local_objects(*self.local_roots())
        else:
            objects = self.minio.list_objects()
        self._objects_by_key = index_objects(objects)
        pairs = pair_objects(objects)
        for pair in pairs:
            if pair.dclx:
                pair.cached_dclx = (
                    resolve_local_path(self.local_roots(), pair.dclx.key) is not None
                    or self.cache.has(pair.dclx.key)
                )
            if pair.pdf:
                pair.cached_pdf = (
                    resolve_local_path(self.local_roots(), pair.pdf.key) is not None
                    or self.cache.has(pair.pdf.key)
                )
            # Preserve manual page counts / pairings from previous listing when possible
            for old in self._pairs_cache:
                if old.document_id == pair.document_id:
                    if old.page_count is not None and old.page_count_source == "manual":
                        pair.page_count = old.page_count
                        pair.page_count_source = "manual"
                    if old.pairing_status == "manual" and old.manual_pdf_key:
                        try:
                            apply_manual_pairing(
                                [pair],
                                pair.document_id,
                                old.manual_pdf_key,
                                self._objects_by_key,
                            )
                        except KeyError:
                            pass
        self._pairs_cache = pairs
        return pairs

    def get_pairs(self, source: Optional[DocumentSource] = None) -> List[DocumentPair]:
        if source is not None and source != self._source:
            return self.refresh_pairs(source)
        if not self._pairs_cache:
            return self.refresh_pairs(self._source)
        return self._pairs_cache

    def manual_pair(
        self, document_id: str, pdf_key: Optional[str], page_count: Optional[int]
    ) -> DocumentPair:
        pairs = self.get_pairs()
        pair = apply_manual_pairing(
            pairs, document_id, pdf_key, self._objects_by_key, page_count
        )
        return pair

    # ------------------------------------------------------------------ jobs
    def get_job(self, job_id: str) -> JobSummary:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        return self._jobs[job_id]

    def list_runs(self) -> List[Dict[str, Any]]:
        runs_dir = self.settings.runs_dir
        runs_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            try:
                items.append(json.loads(path.read_text()))
            except Exception:  # noqa: BLE001
                continue
        return items

    def _log(self, job: JobSummary, message: str) -> None:
        # Never log secrets
        entry = f"[{_now()}] {message}"
        job.logs.append(entry)
        job.message = message

    def _set_stage(self, job: JobSummary, stage: str, progress: float) -> None:
        job.stage = stage
        job.progress = progress

    def start_download(self, req: DownloadRequest) -> JobSummary:
        if self._bench_lock.locked():
            raise RuntimeError(
                "A benchmark is running. Download after it finishes so timings are not disturbed."
            )
        job_id = str(uuid.uuid4())
        job = JobSummary(
            job_id=job_id,
            status=JobStatus.queued,
            stage="queued",
            progress=0.0,
            message="Download queued",
        )
        self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_download, args=(job_id, req), daemon=True
        )
        thread.start()
        return job

    def start_process(self, req: ProcessRequest) -> JobSummary:
        if self._bench_lock.locked():
            raise RuntimeError(
                "Another benchmark job is already running. Measured jobs run sequentially."
            )
        job_id = str(uuid.uuid4())
        job = JobSummary(
            job_id=job_id,
            status=JobStatus.queued,
            stage="queued",
            progress=0.0,
            message="Process job queued",
        )
        self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_process, args=(job_id, req), daemon=True
        )
        thread.start()
        return job

    # -------------------------------------------------------------- download
    def _run_download(self, job_id: str, req: DownloadRequest) -> None:
        job = self._jobs[job_id]
        job.status = JobStatus.running
        try:
            pairs = {p.document_id: p for p in self.get_pairs()}
            selected = [pairs[i] for i in req.document_ids if i in pairs]
            if not selected:
                raise RuntimeError("No valid documents selected.")
            total = len(selected)
            for i, pair in enumerate(selected):
                self._set_stage(job, "download", (i / max(total, 1)))
                if not pair.dclx:
                    raise RuntimeError(f"{pair.document_name}: missing DocLang object.")
                dest = self.cache.ensure_parent(pair.dclx.key)
                if self.cache.has(pair.dclx.key):
                    self._log(job, f"Cache hit DocLang: {pair.dclx.key}")
                else:
                    self._log(job, f"Downloading DocLang: {pair.dclx.key}")
                    self.minio.download_object(pair.dclx.key, dest)
                pair.cached_dclx = True
                if req.include_pdfs and pair.pdf:
                    pdf_dest = self.cache.ensure_parent(pair.pdf.key)
                    if self.cache.has(pair.pdf.key):
                        self._log(job, f"Cache hit PDF (page-count only): {pair.pdf.key}")
                    else:
                        self._log(
                            job,
                            f"Downloading PDF for page counting only: {pair.pdf.key}",
                        )
                        self.minio.download_object(pair.pdf.key, pdf_dest)
                    pair.cached_pdf = True
                    count, source = count_pdf_pages(pdf_dest)
                    if count is not None:
                        pair.page_count = count
                        pair.page_count_source = "pdf"
                    else:
                        pair.page_count_source = "unavailable"
                        self._log(job, f"PDF page count failed ({source}) for {pair.document_name}")
            self._set_stage(job, "done", 1.0)
            job.status = JobStatus.completed
            self._log(job, "Download complete.")
            job.result = RunResult(
                job_id=job_id,
                status=JobStatus.completed,
                mode=RunMode.staged,
                settings=ProcessSettings(),
                documents=selected,
                created_at=_now(),
                finished_at=_now(),
                logs=list(job.logs),
            )
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.failed
            job.error = str(exc)
            self._log(job, f"FAILED: {exc}")

    # --------------------------------------------------------------- process
    def _run_process(self, job_id: str, req: ProcessRequest) -> None:
        acquired = self._bench_lock.acquire(blocking=False)
        if not acquired:
            job = self._jobs[job_id]
            job.status = JobStatus.failed
            job.error = "Another benchmark is running."
            return
        job = self._jobs[job_id]
        job.status = JobStatus.running
        errors: List[str] = []
        try:
            pairs_map = {p.document_id: p for p in self.get_pairs()}
            selected = [pairs_map[i] for i in req.document_ids if i in pairs_map]
            if not selected:
                raise RuntimeError("No valid documents selected.")

            skip_embedding = req.settings.skip_embedding
            embedder: Optional[EmbeddingService] = None
            embed_meta: Dict[str, Any] = {
                "skipped": skip_embedding,
                "reason": (
                    "Cost-estimate mode: tokens counted for paid embedding price comparison; "
                    "vectors not generated."
                    if skip_embedding
                    else None
                ),
            }
            if skip_embedding:
                self._log(
                    job,
                    "Cost-estimate mode: skipping model load and embedding "
                    "(load → chunk → tokenize → cost only).",
                )
                self._set_stage(job, "cost_estimate", 0.05)
            else:
                embedder = EmbeddingService.get(self.settings.embedding_model)
                self._set_stage(job, "model_warmup", 0.05)
                embed_meta = embedder.ensure_loaded(log=lambda m: self._log(job, m))
                embed_meta["skipped"] = False

            trial_payloads: List[Dict[str, Any]] = []
            all_chunks: List[ChunkInspection] = []
            preview_md: Dict[str, str] = {}
            cache_hits: List[str] = []
            cache_misses: List[str] = []
            partial = False

            for trial_idx in range(req.trials):
                self._log(job, f"Starting trial {trial_idx + 1}/{req.trials}")
                trial = self._single_trial(
                    job,
                    selected,
                    req,
                    embedder,
                    cache_hits,
                    cache_misses,
                    preview_md,
                    errors,
                )
                trial_payloads.append(trial)
                if trial.get("chunks"):
                    all_chunks = trial["chunks"]
                if trial.get("partial"):
                    partial = True
                self._set_stage(
                    job, "trial", (trial_idx + 1) / max(req.trials, 1) * 0.9 + 0.05
                )

            # Aggregate median timings
            stage_names = [
                "download",
                "load",
                "chunk",
                "tokenize",
                "embed",
                "process_excluding_download",
                "download_inclusive",
            ]
            median_timings: List[StageTiming] = []
            for name in stage_names:
                values = [
                    t["timings"].get(name)
                    for t in trial_payloads
                    if t.get("timings", {}).get(name) is not None
                ]
                if values:
                    median_timings.append(
                        StageTiming(
                            name=name,
                            seconds=float(statistics.median(values)),
                            measured=True,
                            label="measured_median" if req.trials > 1 else "measured",
                        )
                    )

            last = trial_payloads[-1] if trial_payloads else {}
            pages_total = int(last.get("pages_total", 0))
            cost = compute_costs(
                pages_total,
                last.get("paid_tokens_total"),
                req.settings.cost,
            )
            load_s = float(last.get("timings", {}).get("load", 0.0) or 0.0)
            downstream = float(
                (last.get("timings", {}).get("chunk", 0) or 0)
                + (last.get("timings", {}).get("tokenize", 0) or 0)
                + (last.get("timings", {}).get("embed", 0) or 0)
            )
            time_savings = compute_time_savings(load_s, downstream, req.settings.cost)

            errors = list(dict.fromkeys(errors))
            status = JobStatus.partial if partial or errors else JobStatus.completed
            result = RunResult(
                job_id=job_id,
                status=status,
                mode=req.settings.run_mode,
                settings=req.settings,
                documents=selected,
                pages_total=pages_total,
                chunks_total=int(last.get("chunks_total", 0)),
                local_tokens_total=int(last.get("local_tokens_total", 0)),
                paid_tokens_total=last.get("paid_tokens_total"),
                vectors_generated=int(last.get("vectors_generated", 0)),
                stage_timings=[
                    StageTiming(name=k, seconds=float(v), measured=True)
                    for k, v in (last.get("timings") or {}).items()
                ],
                median_stage_timings=median_timings,
                trials=[{k: v for k, v in t.items() if k != "chunks"} for t in trial_payloads],
                embedding_meta={**embed_meta, **(last.get("embedding_meta") or {})},
                cost=cost,
                time_savings=time_savings,
                chunks=all_chunks,
                preview_markdown=preview_md,
                logs=list(job.logs),
                errors=errors,
                partial=partial or bool(errors),
                cache_hits=cache_hits,
                cache_misses=cache_misses,
                created_at=_now(),
                finished_at=_now(),
            )
            self._persist_run(result)
            job.result = result
            job.status = status
            self._set_stage(job, "done", 1.0)
            self._log(
                job,
                f"Job {status.value}. pages={pages_total} chunks={result.chunks_total} "
                f"local_tokens={result.local_tokens_total}",
            )
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.failed
            job.error = str(exc)
            self._log(job, f"FAILED: {exc}")
            # Keep partial result marker if any
            if job.result is None:
                job.result = RunResult(
                    job_id=job_id,
                    status=JobStatus.failed,
                    mode=req.settings.run_mode,
                    settings=req.settings,
                    documents=[],
                    errors=[str(exc)],
                    partial=True,
                    logs=list(job.logs),
                    created_at=_now(),
                    finished_at=_now(),
                )
        finally:
            self._bench_lock.release()

    def _single_trial(
        self,
        job: JobSummary,
        selected: List[DocumentPair],
        req: ProcessRequest,
        embedder: Optional[EmbeddingService],
        cache_hits: List[str],
        cache_misses: List[str],
        preview_md: Dict[str, str],
        errors: List[str],
    ) -> Dict[str, Any]:
        timings: Dict[str, float] = {
            "download": 0.0,
            "load": 0.0,
            "chunk": 0.0,
            "tokenize": 0.0,
            "embed": 0.0,
        }
        download_inclusive_start = time.perf_counter()
        all_chunk_objs = []
        pages_total = 0
        partial = False
        chunks_inspection: List[ChunkInspection] = []

        for pair in selected:
            if not pair.dclx:
                errors.append(f"{pair.document_name}: no DocLang key")
                partial = True
                continue

            # Resolve DocLang path: local disk first, optional MinIO download
            local_dclx = resolve_local_path(self.local_roots(), pair.dclx.key)
            dclx_path = local_dclx or self.cache.path_for(pair.dclx.key)
            on_disk = local_dclx is not None or self.cache.has(pair.dclx.key)
            use_local_only = req.settings.source == DocumentSource.local

            if use_local_only:
                if not on_disk:
                    raise RuntimeError(
                        f"Local source: DocLang file not found on disk for {pair.dclx.key}. "
                        f"Place it under {self.settings.cache_dir} or {self.settings.local_docs_dir}."
                    )
                cache_hits.append(pair.dclx.key)
                self._log(job, f"Using local DocLang (no MinIO): {dclx_path}")
                pair.cached_dclx = True
            else:
                need_download = req.force_redownload or not on_disk
                if req.settings.run_mode == RunMode.staged and need_download:
                    raise RuntimeError(
                        f"Staged mode requires cached DocLang for {pair.dclx.key}. "
                        "Download selected artifacts first, or switch Source to Local."
                    )
                if need_download or req.settings.run_mode == RunMode.download_inclusive:
                    if on_disk and not req.force_redownload:
                        if req.settings.run_mode == RunMode.download_inclusive:
                            self._log(
                                job,
                                f"Re-downloading for inclusive timing: {pair.dclx.key}",
                            )
                            dest = self.cache.ensure_parent(pair.dclx.key)
                            _, elapsed = self.minio.download_object(pair.dclx.key, dest)
                            timings["download"] += elapsed
                            cache_misses.append(pair.dclx.key)
                            dclx_path = dest
                        else:
                            cache_hits.append(pair.dclx.key)
                            self._log(job, f"Cache hit: {pair.dclx.key}")
                    else:
                        self._log(job, f"Downloading: {pair.dclx.key}")
                        dest = self.cache.ensure_parent(pair.dclx.key)
                        _, elapsed = self.minio.download_object(pair.dclx.key, dest)
                        timings["download"] += elapsed
                        cache_misses.append(pair.dclx.key)
                        dclx_path = dest
                    pair.cached_dclx = True
                else:
                    cache_hits.append(pair.dclx.key)
                    self._log(job, f"Using cached DocLang: {pair.dclx.key}")

            # PDF page count outside measured DocLang pipeline (unless already known)
            if pair.page_count is None and pair.pdf:
                local_pdf = resolve_local_path(self.local_roots(), pair.pdf.key)
                pdf_path = local_pdf or self.cache.path_for(pair.pdf.key)
                if local_pdf is None and not self.cache.has(pair.pdf.key):
                    if use_local_only:
                        self._log(
                            job,
                            f"No local PDF for page count ({pair.pdf.key}); "
                            "will try DocLang pages or leave unavailable.",
                        )
                    else:
                        self._log(
                            job,
                            f"Fetching PDF for page count only (excluded from pipeline timing): {pair.pdf.key}",
                        )
                        self.minio.download_object(
                            pair.pdf.key, self.cache.ensure_parent(pair.pdf.key)
                        )
                        pair.cached_pdf = True
                        pdf_path = self.cache.path_for(pair.pdf.key)
                if pdf_path and Path(pdf_path).is_file():
                    count, source = count_pdf_pages(Path(pdf_path))
                    if count is not None:
                        pair.page_count = count
                        pair.page_count_source = "pdf"  # type: ignore[assignment]
                    else:
                        pair.page_count_source = "unavailable"  # type: ignore[assignment]

            # Load DocLang (PDF access not required)
            artifacts_dir = (
                self.settings.artifacts_dir / pair.document_id.replace("/", "_")
            )
            self._log(job, f"Loading DocLang archive: {pair.document_name}…")
            try:
                document, load_elapsed, _meta = load_doclang_archive(
                    dclx_path, artifacts_dir=artifacts_dir
                )
                timings["load"] += load_elapsed
                pair.load_status = "loaded"
                self._log(
                    job,
                    f"Loaded {pair.document_name} in {load_elapsed:.2f}s",
                )
            except DocLangLoadError as exc:
                errors.append(str(exc))
                pair.load_status = "failed"
                partial = True
                continue

            if pair.page_count is None:
                count, source = pages_from_doclang_document(document)
                if count is not None:
                    pair.page_count = count
                    pair.page_count_source = source  # type: ignore[assignment]

            if pair.page_count is None:
                errors.append(
                    f"{pair.document_name}: page count unavailable, so extraction cost "
                    "excludes this document. Set the page count manually in the document list."
                )
                partial = True
            pages_total += int(pair.page_count or 0)

            self._log(job, f"Chunking {pair.document_name}…")
            chunks, markdown, chunk_elapsed = chunk_document(
                document,
                pair.document_id,
                chunk_size=req.settings.chunk_size,
                chunk_overlap=req.settings.chunk_overlap,
            )
            timings["chunk"] += chunk_elapsed
            self._log(
                job,
                f"Chunked {pair.document_name}: {len(chunks)} chunks in {chunk_elapsed:.2f}s",
            )
            preview_md[pair.document_id] = markdown[:20000]
            all_chunk_objs.extend(chunks)

        # Tokenize
        texts = [c.text for c in all_chunk_objs]
        self._log(job, f"Counting tokens for {len(texts)} chunks…")
        local_results, paid_results, tok_elapsed = count_chunks(
            texts,
            self._local_tokenizer,
            req.settings.cost.paid_token_method,
            paid_model_label=req.settings.cost.paid_embedding_model or "paid-model",
        )
        timings["tokenize"] = tok_elapsed
        self._log(job, f"Token counting done in {tok_elapsed:.2f}s")

        for chunk, loc, paid in zip(all_chunk_objs, local_results, paid_results):
            chunks_inspection.append(
                ChunkInspection(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    local_token_count=loc.token_count,
                    paid_token_count=paid.token_count,
                    truncated=loc.truncated,
                    truncation_note=loc.truncation_note,
                )
            )

        trunc_flags = [r.truncated for r in local_results]
        if any(trunc_flags):
            n = sum(1 for t in trunc_flags if t)
            note = (
                f"Detected {n} chunk(s) exceeding embedding max length "
                "(relevant if you later embed with this model)."
            )
            if req.settings.skip_embedding:
                self._log(job, note)
            else:
                errors.append(
                    f"Detected {n} chunk(s) exceeding embedding max length; "
                    "flagged, not silently discarded."
                )
                partial = True

        if req.settings.skip_embedding:
            self._set_stage(job, "cost_estimate", 0.9)
            self._log(
                job,
                "Skipping embedding — cost estimate uses token counts only.",
            )
            timings["embed"] = 0.0
            vectors_generated = 0
            embedding_meta: Dict[str, Any] = {
                "skipped": True,
                "model": self.settings.embedding_model,
                "reason": "skip_embedding cost-estimate mode",
                "batch_size": req.settings.embedding_batch_size,
                "num_inputs": len(texts),
            }
        else:
            if embedder is None:
                raise RuntimeError("Embedding service is required when skip_embedding is false.")
            self._set_stage(job, "embed", max(job.progress, 0.55))
            self._log(
                job,
                f"Embedding {len(texts)} chunks on {embedder.device} "
                f"(batch_size={req.settings.embedding_batch_size}) — "
                "this can take several minutes on CPU…",
            )
            last_log_at = [0]

            def _embed_progress(done: int, total: int, elapsed: float) -> None:
                step = max(32, total // 20) if total else 1
                if done < total and done - last_log_at[0] < step:
                    frac = 0.55 + 0.40 * (done / max(total, 1))
                    self._set_stage(job, "embed", frac)
                    return
                last_log_at[0] = done
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                frac = 0.55 + 0.40 * (done / max(total, 1))
                self._set_stage(job, "embed", frac)
                self._log(
                    job,
                    f"Embedding progress: {done}/{total} chunks "
                    f"({100.0 * done / max(total, 1):.0f}%) · "
                    f"{elapsed:.0f}s elapsed · ~{eta:.0f}s remaining",
                )

            embed_result = embedder.embed_documents(
                texts,
                batch_size=req.settings.embedding_batch_size,
                prechecked_truncated=trunc_flags,
                on_progress=_embed_progress,
            )
            timings["embed"] = embed_result.elapsed_seconds
            vectors_generated = embed_result.vectors
            embedding_meta = embed_result.meta
            self._log(
                job,
                f"Embedding finished: {embed_result.vectors} vectors "
                f"in {embed_result.elapsed_seconds:.2f}s",
            )

        timings["process_excluding_download"] = (
            timings["load"] + timings["chunk"] + timings["tokenize"] + timings["embed"]
        )
        timings["download_inclusive"] = time.perf_counter() - download_inclusive_start

        local_total = sum(r.token_count for r in local_results)
        paid_total = sum(r.token_count for r in paid_results)

        return {
            "timings": timings,
            "pages_total": pages_total,
            "chunks_total": len(all_chunk_objs),
            "local_tokens_total": local_total,
            "paid_tokens_total": paid_total,
            "vectors_generated": vectors_generated,
            "embedding_meta": embedding_meta,
            "chunks": chunks_inspection,
            "partial": partial,
            "token_meta": {
                "local_tokenizer": self._local_tokenizer.model_name,
                "local_method": "transformers_auto_tokenizer_exact",
                "local_exact": True,
                "paid_method": req.settings.cost.paid_token_method,
                "paid_model": req.settings.cost.paid_embedding_model,
            },
        }

    def _persist_run(self, result: RunResult) -> Path:
        path = self.settings.runs_dir / f"{result.job_id}.json"
        # Omit huge chunk texts from history? Keep them for inspection as required.
        path.write_text(result.model_dump_json(indent=2))
        return path


_manager: Optional[JobManager] = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
