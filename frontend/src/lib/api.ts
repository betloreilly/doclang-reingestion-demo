export type ObjectInfo = {
  key: string;
  size: number;
  last_modified?: string | null;
};

export type DocumentPair = {
  document_id: string;
  document_name: string;
  dclx?: ObjectInfo | null;
  pdf?: ObjectInfo | null;
  json_obj?: ObjectInfo | null;
  pairing_status: string;
  ambiguous_pdf_keys: string[];
  ambiguous_json_keys: string[];
  manual_pdf_key?: string | null;
  page_count?: number | null;
  page_count_source?: string | null;
  cached_dclx: boolean;
  cached_pdf: boolean;
  load_status?: string | null;
};

export type ConnectionStatus = {
  configured: boolean;
  connected: boolean;
  bucket: string;
  prefix: string;
  endpoint_host: string;
  secure: boolean;
  env_file_present: boolean;
  errors: string[];
  object_counts: Record<string, number>;
  env_path?: string;
  local_cache_dir?: string;
  local_docs_dir?: string;
};

export type CompareExtractionVendor = {
  provider: string;
  price_per_1000_pages: number;
  note?: string;
};

export type CostSettings = {
  extraction_provider?: string;
  extraction_price_per_1000_pages: number;
  /** Alternate extraction vendors for side-by-side comparison (Unstructured, Azure, Snowflake, …). */
  compare_extraction_vendors?: CompareExtractionVendor[];
  /** @deprecated Prefer compare_extraction_vendors. */
  compare_extraction_provider?: string;
  /** @deprecated Prefer compare_extraction_vendors (USD / page). */
  compare_extraction_price_per_page?: number | null;
  paid_embedding_model: string;
  embedding_price_per_million_tokens: number | null;
  future_reingestions: number;
  paid_token_method:
    | "tiktoken_cl100k"
    | "tiktoken_o200k"
    | "chars_div_4"
    | "same_as_local";
  extraction_baseline_seconds: number | null;
  extraction_baseline_note: string;
};

export type ProcessSettings = {
  chunk_size: number;
  chunk_overlap: number;
  embedding_batch_size: number;
  run_mode: "staged" | "download_inclusive";
  source: "minio" | "local";
  skip_embedding: boolean;
  cost: CostSettings;
};

export type RunResult = {
  job_id: string;
  status: string;
  mode: string;
  settings: ProcessSettings;
  documents: DocumentPair[];
  pages_total: number;
  chunks_total: number;
  local_tokens_total: number;
  paid_tokens_total?: number | null;
  vectors_generated: number;
  stage_timings: { name: string; seconds: number; label?: string }[];
  median_stage_timings: { name: string; seconds: number; label?: string }[];
  embedding_meta: Record<string, unknown>;
  cost?: {
    pages: number;
    extraction_price_per_1000_pages: number;
    avoided_extraction_per_reingestion: number;
    paid_embedding_tokens?: number | null;
    embedding_price_per_million_tokens?: number | null;
    embedding_estimate_per_reingestion?: number | null;
    hypothetical_fresh_pdf_cost?: number | null;
    prepared_doclang_cost?: number | null;
    extraction_to_embedding_ratio?: number | null;
    embedding_pct_of_extraction?: number | null;
    extraction_pct_of_fresh_total?: number | null;
    embedding_pct_of_fresh_total?: number | null;
    future_extraction_savings?: number;
    future_reingestions?: number;
    embedding_pricing_available: boolean;
    notes: string[];
  } | null;
  time_savings?: {
    available: boolean;
    baseline_seconds?: number | null;
    doclang_load_seconds?: number | null;
    estimated_extraction_stage_savings_seconds?: number | null;
    modeled_full_pipeline_fresh_seconds?: number | null;
    modeled_full_pipeline_prepared_seconds?: number | null;
    note: string;
  } | null;
  chunks: {
    chunk_id: string;
    document_id: string;
    text: string;
    local_token_count: number;
    paid_token_count?: number | null;
    truncated: boolean;
    truncation_note?: string | null;
  }[];
  preview_markdown: Record<string, string>;
  logs: string[];
  errors: string[];
  partial: boolean;
  cache_hits: string[];
  cache_misses: string[];
};

export type JobSummary = {
  job_id: string;
  status: string;
  stage: string;
  progress: number;
  message: string;
  logs: string[];
  result?: RunResult | null;
  error?: string | null;
};

export type RecallPipelineKey = "doclang" | "unstructured";

export type RecallSummaryBlock = {
  n: number;
  hit_at: Record<string, number>;
  recall_at: Record<string, number>;
  mrr: number;
};

export type RecallRetrieval = {
  overall: RecallSummaryBlock;
  by_type: Record<string, RecallSummaryBlock>;
};

export type RecallPipeline = {
  label: string;
  chunks: number;
  tokens: number;
  pages_with_text: number;
  extraction_seconds_measured: number;
  evidence_coverage: {
    evidence_items: number;
    mean: number;
    share_at_least_90pct: number;
  };
  retrieval: { global: RecallRetrieval; per_document: RecallRetrieval } | null;
};

export type RecallHit = {
  doc_name: string;
  page: number;
  score: number;
  relevant: boolean;
  snippet: string;
};

export type RecallQuestionPipeline = {
  evidence_coverage?: number | null;
  global_first_rank?: number | null;
  per_document_first_rank?: number | null;
  global_top?: RecallHit[];
  per_document_top?: RecallHit[];
};

export type RecallQuestion = {
  financebench_id: string;
  doc_name: string;
  question_type: string;
  question: string;
  answer: string;
  evidence_pages: number[];
  doclang: RecallQuestionPipeline;
  unstructured: RecallQuestionPipeline;
};

export type RecallRun = {
  run_id: string;
  created_at: string;
  finished_at: string;
  estimate_only: boolean;
  retrieval_backend?: string;
  settings: {
    chunk_size: number;
    chunk_overlap: number;
    max_documents?: number | null;
    unstructured_strategy: string;
    ks: number[];
    retrieval_unit: string;
  };
  dataset: {
    name: string;
    repo: string;
    questions_total: number;
    questions_evaluated: number;
    documents_evaluated: number;
  };
  embedding: {
    model: string;
    price_per_million: number;
    tokens_total: number;
    tokens_uncached: number;
    estimated_cost_full_usd: number;
    estimated_cost_remaining_usd: number;
    billed_tokens_this_run: number;
    cost_this_run_usd: number;
  };
  pipelines: Record<RecallPipelineKey, RecallPipeline>;
  doclang_page_realignment?: {
    doc_name: string;
    doclang_pages: number;
    pdf_pages: number;
    pages_renumbered: number;
  }[];
  excluded_documents: string[];
  errors: string[];
  partial: boolean;
  questions?: RecallQuestion[];
};

export type RecallStatus = {
  dataset_present: boolean;
  dataset_repo: string;
  openai_configured: boolean;
  embedding_model: string;
  embedding_price_per_million: number;
  unstructured_available: boolean;
  unstructured_cached_documents: number;
  embeddings_cached?: {
    doclang_documents: number;
    unstructured_documents: number;
    queries: boolean;
    ready: boolean;
  };
  opensearch_ready?: boolean;
  running: boolean;
  opensearch?: {
    configured: boolean;
    url: string;
    index_prefix: string;
    connected: boolean;
    cluster_name?: string | null;
    version?: string | null;
    indexes: Record<
      string,
      { name: string; exists: boolean; docs: number }
    >;
    error?: string | null;
  };
};

export type RecallJob = {
  job_id: string;
  status: string;
  stage: string;
  progress: number;
  message: string;
  logs: string[];
  error?: string | null;
  run_id?: string | null;
};

// Prefer same-origin /api (Next rewrite → FastAPI). Override with NEXT_PUBLIC_API_BASE if needed.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (Array.isArray(body.detail)) {
        detail = body.detail
          .map((d: { msg?: string }) => d.msg ?? JSON.stringify(d))
          .join("; ");
      } else {
        detail = body.detail || JSON.stringify(body);
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const client = {
  connection: () => api<ConnectionStatus>("/api/connection"),
  reloadConnection: () =>
    api<ConnectionStatus>("/api/connection/reload", { method: "POST" }),
  documents: (refresh = false, source: "minio" | "local" = "minio") =>
    api<{
      documents: DocumentPair[];
      count: number;
      source: string;
      local_roots?: string[];
    }>(
      `/api/documents?refresh=${refresh ? "true" : "false"}&source=${source}`
    ),
  manualPair: (body: {
    document_id: string;
    pdf_key?: string | null;
    page_count?: number | null;
  }) =>
    api<DocumentPair>("/api/documents/manual-pair", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  download: (document_ids: string[], include_pdfs = true) =>
    api<JobSummary>("/api/jobs/download", {
      method: "POST",
      body: JSON.stringify({ document_ids, include_pdfs }),
    }),
  process: (document_ids: string[], settings: ProcessSettings, trials = 1) =>
    api<JobSummary>("/api/jobs/process", {
      method: "POST",
      body: JSON.stringify({ document_ids, settings, trials }),
    }),
  job: (jobId: string) => api<JobSummary>(`/api/jobs/${jobId}`),
  runs: () => api<{ runs: RunResult[] }>("/api/runs"),
  exportUrl: (runId: string, format: "json" | "csv") =>
    `${API_BASE}/api/runs/${runId}/export?format=${format}`,
  recallStatus: () => api<RecallStatus>("/api/recall/status"),
  startRecall: (body: {
    estimate_only: boolean;
    chunk_size: number;
    chunk_overlap: number;
    max_documents?: number | null;
  }) =>
    api<RecallJob>("/api/recall/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  recallJob: (jobId: string) => api<RecallJob>(`/api/recall/jobs/${jobId}`),
  recallRuns: () => api<{ runs: RecallRun[] }>("/api/recall/runs"),
  recallRun: (runId: string) => api<RecallRun>(`/api/recall/runs/${runId}`),
  startOpensearchIngest: (body?: {
    chunk_size?: number;
    chunk_overlap?: number;
    recreate_indexes?: boolean;
    max_documents?: number | null;
  }) =>
    api<RecallJob>("/api/recall/opensearch/ingest", {
      method: "POST",
      body: JSON.stringify({
        chunk_size: body?.chunk_size ?? 1000,
        chunk_overlap: body?.chunk_overlap ?? 150,
        recreate_indexes: body?.recreate_indexes ?? false,
        max_documents: body?.max_documents ?? null,
      }),
    }),
  opensearchIngestJob: (jobId: string) =>
    api<RecallJob>(`/api/recall/opensearch/ingest/${jobId}`),
  evaluateOpensearch: (maxDocuments?: number | null) =>
    api<RecallRun>(
      `/api/recall/opensearch/evaluate${
        maxDocuments ? `?max_documents=${maxDocuments}` : ""
      }`,
      { method: "POST" }
    ),
};
