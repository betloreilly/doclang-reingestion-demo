"""FastAPI entrypoint for DocLang Reingestion Savings."""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .config import ENV_FILE, env_file_present, get_settings, reload_settings
from .jobs import get_manager
from .schemas import (
    DownloadRequest,
    ManualPairRequest,
    ProcessRequest,
)

app = FastAPI(
    title="DocLang Reingestion Savings",
    description=(
        "Compare Docling SaaS PDF extraction cost with embedding cost: "
        "pages from the PDF, chunks and tokens from existing DocLang archives."
    ),
    version="1.0.0",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "service": "doclang-reingestion-savings"}


@app.get("/api/connection")
def connection_status() -> Dict[str, Any]:
    mgr = get_manager()
    status = mgr.minio.status()
    payload = status.model_dump()
    payload["env_path"] = str(ENV_FILE)
    payload["env_file_present"] = env_file_present()
    settings = get_settings()
    payload["local_cache_dir"] = str(settings.cache_dir)
    payload["local_docs_dir"] = str(settings.local_docs_dir)
    # Never expose credentials
    return payload


@app.post("/api/connection/reload")
def reload_connection() -> Dict[str, Any]:
    reload_settings()
    global settings
    settings = get_settings()
    # Reset manager so it picks up new settings
    from . import jobs as jobs_mod

    jobs_mod._manager = None
    return connection_status()


@app.get("/api/documents")
def list_documents(refresh: bool = False, source: str = "minio") -> Dict[str, Any]:
    from .schemas import DocumentSource

    mgr = get_manager()
    try:
        src = DocumentSource(source)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="source must be 'minio' or 'local'"
        ) from exc
    try:
        pairs = (
            mgr.refresh_pairs(src)
            if refresh or src != getattr(mgr, "_source", None)
            else mgr.get_pairs(src)
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    settings = get_settings()
    return {
        "documents": [p.model_dump() for p in pairs],
        "count": len(pairs),
        "source": src.value,
        "local_roots": [str(settings.cache_dir), str(settings.local_docs_dir)],
    }


@app.post("/api/documents/manual-pair")
def manual_pair(req: ManualPairRequest) -> Dict[str, Any]:
    mgr = get_manager()
    try:
        pair = mgr.manual_pair(req.document_id, req.pdf_key, req.page_count)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return pair.model_dump()


@app.post("/api/jobs/download")
def start_download(req: DownloadRequest) -> Dict[str, Any]:
    if not req.document_ids:
        raise HTTPException(status_code=400, detail="Select at least one document.")
    mgr = get_manager()
    try:
        job = mgr.start_download(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.model_dump()


@app.post("/api/jobs/process")
def start_process(req: ProcessRequest) -> Dict[str, Any]:
    if not req.document_ids:
        raise HTTPException(status_code=400, detail="Select at least one document.")
    mgr = get_manager()
    try:
        job = mgr.start_process(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.model_dump()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    mgr = get_manager()
    try:
        job = mgr.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return job.model_dump()


@app.get("/api/runs")
def list_runs() -> Dict[str, Any]:
    mgr = get_manager()
    return {"runs": mgr.list_runs()}


def _run_path(run_id: str):
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    path = get_manager().settings.runs_dir / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return path


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    return json.loads(_run_path(run_id).read_text())


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: str, format: str = "json"):
    data = json.loads(_run_path(run_id).read_text())
    if format == "json":
        return JSONResponse(
            content=data,
            headers={
                "Content-Disposition": f'attachment; filename="{run_id}.json"'
            },
        )
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "job_id",
                "status",
                "mode",
                "pages_total",
                "chunks_total",
                "local_tokens_total",
                "paid_tokens_total",
                "vectors_generated",
                "embedding_skipped",
                "paid_token_method",
                "extraction_price_per_1000_pages",
                "embedding_price_per_million_tokens",
                "extraction_cost_usd",
                "embedding_cost_usd",
                "total_cost_usd",
                "extraction_to_embedding_ratio",
                "extraction_pct_of_total",
                "embedding_pct_of_total",
                "process_excluding_download_s",
                "download_inclusive_s",
                "partial",
            ]
        )
        timings = {t["name"]: t["seconds"] for t in data.get("stage_timings", [])}
        cost = data.get("cost") or {}
        run_settings = data.get("settings") or {}
        cost_settings = run_settings.get("cost") or {}
        writer.writerow(
            [
                data.get("job_id"),
                data.get("status"),
                data.get("mode"),
                data.get("pages_total"),
                data.get("chunks_total"),
                data.get("local_tokens_total"),
                data.get("paid_tokens_total"),
                data.get("vectors_generated"),
                run_settings.get("skip_embedding"),
                cost_settings.get("paid_token_method"),
                cost.get("extraction_price_per_1000_pages"),
                cost.get("embedding_price_per_million_tokens"),
                cost.get("avoided_extraction_per_reingestion"),
                cost.get("embedding_estimate_per_reingestion"),
                cost.get("hypothetical_fresh_pdf_cost"),
                cost.get("extraction_to_embedding_ratio"),
                cost.get("extraction_pct_of_fresh_total"),
                cost.get("embedding_pct_of_fresh_total"),
                timings.get("process_excluding_download"),
                timings.get("download_inclusive"),
                data.get("partial"),
            ]
        )
        writer.writerow([])
        writer.writerow(["chunk_id", "document_id", "local_tokens", "paid_tokens", "truncated", "text"])
        for ch in data.get("chunks", []):
            writer.writerow(
                [
                    ch.get("chunk_id"),
                    ch.get("document_id"),
                    ch.get("local_token_count"),
                    ch.get("paid_token_count"),
                    ch.get("truncated"),
                    (ch.get("text") or "").replace("\n", "\\n"),
                ]
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{run_id}.csv"'
            },
        )
    raise HTTPException(status_code=400, detail="format must be json or csv")


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    s = get_settings()
    return {
        "embedding_model": s.embedding_model,
        "default_extraction_price_per_1000_pages": 4.0,
        "bucket": s.minio_bucket,
        "prefix": s.minio_prefix,
        "notes": [
            "PDF extraction, DocLang generation, OCR, and layout inference are out of scope.",
            "Embeddings run locally; paid API calls are not executed.",
        ],
    }
