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

export type CostSettings = {
  extraction_provider?: string;
  extraction_price_per_1000_pages: number;
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
};
