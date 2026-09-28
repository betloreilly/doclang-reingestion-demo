"""Pydantic request/response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    partial = "partial"


class RunMode(str, Enum):
    staged = "staged"
    download_inclusive = "download_inclusive"


class DocumentSource(str, Enum):
    minio = "minio"
    local = "local"


class ObjectInfo(BaseModel):
    key: str
    size: int
    last_modified: Optional[str] = None
    etag: Optional[str] = None


class DocumentPair(BaseModel):
    document_id: str
    document_name: str
    dclx: Optional[ObjectInfo] = None
    pdf: Optional[ObjectInfo] = None
    json_obj: Optional[ObjectInfo] = None
    pairing_status: Literal[
        "paired", "dclx_only", "ambiguous_pdf", "ambiguous_json", "manual"
    ] = "dclx_only"
    ambiguous_pdf_keys: List[str] = Field(default_factory=list)
    ambiguous_json_keys: List[str] = Field(default_factory=list)
    manual_pdf_key: Optional[str] = None
    page_count: Optional[int] = None
    page_count_source: Optional[
        Literal["pdf", "doclang_pages", "manual", "unavailable"]
    ] = None
    cached_dclx: bool = False
    cached_pdf: bool = False
    load_status: Optional[str] = None


class ConnectionStatus(BaseModel):
    configured: bool
    connected: bool
    bucket: str
    prefix: str
    endpoint_host: str
    secure: bool
    env_file_present: bool
    errors: List[str] = Field(default_factory=list)
    object_counts: Dict[str, int] = Field(default_factory=dict)


class ManualPairRequest(BaseModel):
    document_id: str
    pdf_key: Optional[str] = None
    page_count: Optional[int] = None


class CompareExtractionVendor(BaseModel):
    """Alternate PDF extraction vendor used only for cost comparison (not executed)."""

    provider: str
    price_per_1000_pages: float = Field(ge=0)
    note: str = ""


def default_compare_extraction_vendors() -> List["CompareExtractionVendor"]:
    return [
        CompareExtractionVendor(
            provider="Unstructured.io",
            price_per_1000_pages=15.0,
            note="$0.015 / page (pay-as-you-go list rate)",
        ),
        CompareExtractionVendor(
            provider="Azure Document Intelligence (Layout)",
            price_per_1000_pages=10.0,
            note="S0 prebuilt / Layout · $10 / 1,000 pages (Read is $1.50 / 1,000)",
        ),
        CompareExtractionVendor(
            provider="Snowflake AI_PARSE_DOCUMENT (Layout, global)",
            price_per_1000_pages=7.32,
            note="Layout mode, global routing ($8.052 with regional routing)",
        ),
    ]


class CostSettings(BaseModel):
    extraction_provider: str = "Docling SaaS"
    extraction_price_per_1000_pages: float = 4.0
    # Alternate extraction vendors (Unstructured, Azure, Snowflake, …). Same page count.
    compare_extraction_vendors: List[CompareExtractionVendor] = Field(
        default_factory=default_compare_extraction_vendors
    )
    # Legacy single-compare fields (kept so older saved runs / clients still load).
    compare_extraction_provider: Optional[str] = None
    compare_extraction_price_per_page: Optional[float] = None
    paid_embedding_model: str = ""
    embedding_price_per_million_tokens: Optional[float] = None
    future_reingestions: int = 1
    paid_token_method: Literal[
        "tiktoken_cl100k", "tiktoken_o200k", "chars_div_4", "same_as_local"
    ] = "tiktoken_cl100k"
    extraction_baseline_seconds: Optional[float] = None
    extraction_baseline_note: str = ""

    @model_validator(mode="after")
    def _migrate_legacy_compare(self) -> "CostSettings":
        if self.compare_extraction_vendors:
            return self
        if (
            self.compare_extraction_provider
            and self.compare_extraction_price_per_page is not None
        ):
            self.compare_extraction_vendors = [
                CompareExtractionVendor(
                    provider=self.compare_extraction_provider,
                    price_per_1000_pages=self.compare_extraction_price_per_page * 1000.0,
                    note="Migrated from compare_extraction_price_per_page",
                )
            ]
        return self


class ProcessSettings(BaseModel):
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    embedding_batch_size: int = Field(default=8, ge=1, le=64)
    run_mode: RunMode = RunMode.staged
    # local = use files already on disk (cache + LOCAL_DOCS_DIR); never call MinIO
    source: DocumentSource = DocumentSource.minio
    # Cost-estimate mode: load → chunk → tokenize → cost math (no vector generation)
    skip_embedding: bool = True
    cost: CostSettings = Field(default_factory=CostSettings)

    @model_validator(mode="after")
    def _overlap_below_half_chunk(self) -> "ProcessSettings":
        if self.chunk_overlap > self.chunk_size // 2:
            raise ValueError("chunk_overlap must be at most half of chunk_size")
        return self


class DownloadRequest(BaseModel):
    document_ids: List[str]
    include_pdfs: bool = True


class ProcessRequest(BaseModel):
    document_ids: List[str]
    settings: ProcessSettings = Field(default_factory=ProcessSettings)
    trials: int = Field(default=1, ge=1, le=10)
    force_redownload: bool = False


class ChunkInspection(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    local_token_count: int
    paid_token_count: Optional[int] = None
    truncated: bool = False
    truncation_note: Optional[str] = None


class StageTiming(BaseModel):
    name: str
    seconds: float
    measured: bool = True
    label: str = "measured"


class CostBreakdown(BaseModel):
    pages: int
    extraction_price_per_1000_pages: float
    avoided_extraction_per_reingestion: float
    paid_embedding_tokens: Optional[int] = None
    embedding_price_per_million_tokens: Optional[float] = None
    embedding_estimate_per_reingestion: Optional[float] = None
    hypothetical_fresh_pdf_cost: Optional[float] = None
    prepared_doclang_cost: Optional[float] = None
    # extraction_cost / embedding_cost (e.g. 145 means extraction is 145× embedding)
    extraction_to_embedding_ratio: Optional[float] = None
    # embedding as % of extraction (e.g. 0.68 means embedding is 0.68% of extraction)
    embedding_pct_of_extraction: Optional[float] = None
    # extraction as % of (extraction + embedding) fresh-pipeline total
    extraction_pct_of_fresh_total: Optional[float] = None
    embedding_pct_of_fresh_total: Optional[float] = None
    future_extraction_savings: float = 0.0
    future_reingestions: int = 0
    embedding_pricing_available: bool
    notes: List[str] = Field(default_factory=list)


class TimeSavings(BaseModel):
    available: bool
    baseline_seconds: Optional[float] = None
    doclang_load_seconds: Optional[float] = None
    estimated_extraction_stage_savings_seconds: Optional[float] = None
    modeled_full_pipeline_fresh_seconds: Optional[float] = None
    modeled_full_pipeline_prepared_seconds: Optional[float] = None
    note: str = ""


class RunResult(BaseModel):
    job_id: str
    status: JobStatus
    mode: RunMode
    settings: ProcessSettings
    documents: List[DocumentPair]
    pages_total: int = 0
    chunks_total: int = 0
    local_tokens_total: int = 0
    paid_tokens_total: Optional[int] = None
    vectors_generated: int = 0
    stage_timings: List[StageTiming] = Field(default_factory=list)
    median_stage_timings: List[StageTiming] = Field(default_factory=list)
    trials: List[Dict[str, Any]] = Field(default_factory=list)
    embedding_meta: Dict[str, Any] = Field(default_factory=dict)
    cost: Optional[CostBreakdown] = None
    time_savings: Optional[TimeSavings] = None
    chunks: List[ChunkInspection] = Field(default_factory=list)
    preview_markdown: Dict[str, str] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    partial: bool = False
    cache_hits: List[str] = Field(default_factory=list)
    cache_misses: List[str] = Field(default_factory=list)
    created_at: str = ""
    finished_at: Optional[str] = None


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    stage: str
    progress: float
    message: str
    logs: List[str] = Field(default_factory=list)
    result: Optional[RunResult] = None
    error: Optional[str] = None
