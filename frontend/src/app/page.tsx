"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  client,
  type ConnectionStatus,
  type DocumentPair,
  type JobSummary,
  type ProcessSettings,
  type RunResult,
} from "@/lib/api";
import { formatInt, formatSeconds } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge, Input, Label } from "@/components/ui/input";
import { CostComparisonResults } from "@/components/CostComparisonResults";

const defaultSettings: ProcessSettings = {
  chunk_size: 1000,
  chunk_overlap: 150,
  embedding_batch_size: 8,
  run_mode: "staged",
  source: "local",
  skip_embedding: true,
  cost: {
    extraction_provider: "Docling SaaS",
    extraction_price_per_1000_pages: 4.0,
    paid_embedding_model: "text-embedding-3-small",
    embedding_price_per_million_tokens: 0.02,
    future_reingestions: 1,
    paid_token_method: "tiktoken_cl100k",
    extraction_baseline_seconds: null,
    extraction_baseline_note: "",
  },
};

export default function DashboardPage() {
  const [connection, setConnection] = useState<ConnectionStatus | null>(null);
  const [documents, setDocuments] = useState<DocumentPair[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [settings, setSettings] = useState<ProcessSettings>(defaultSettings);
  const [trials, setTrials] = useState(1);
  const [job, setJob] = useState<JobSummary | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [history, setHistory] = useState<RunResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manualPages, setManualPages] = useState<Record<string, string>>({});
  const [manualPdf, setManualPdf] = useState<Record<string, string>>({});
  const [inspectDoc, setInspectDoc] = useState<string>("");
  const [localRoots, setLocalRoots] = useState<string[]>([]);

  const loadConnection = useCallback(async () => {
    try {
      const c = await client.connection();
      setConnection(c);
      if (c.local_cache_dir || c.local_docs_dir) {
        setLocalRoots(
          [c.local_cache_dir, c.local_docs_dir].filter(Boolean) as string[]
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadDocuments = useCallback(
    async (refresh = false, source?: "minio" | "local") => {
      setBusy(true);
      setError(null);
      const src = source ?? settings.source;
      try {
        const res = await client.documents(refresh, src);
        setDocuments(res.documents);
        if (res.local_roots) setLocalRoots(res.local_roots);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [settings.source]
  );

  const loadHistory = useCallback(async () => {
    try {
      const res = await client.runs();
      setHistory(res.runs);
    } catch {
      /* optional */
    }
  }, []);

  useEffect(() => {
    loadConnection();
    loadDocuments(true);
    loadHistory();
  }, [loadConnection, loadDocuments, loadHistory]);

  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed" || job.status === "partial") {
      return;
    }
    const id = setInterval(async () => {
      try {
        const next = await client.job(job.job_id);
        setJob(next);
        if (next.result) setResult(next.result);
        if (
          next.status === "completed" ||
          next.status === "failed" ||
          next.status === "partial"
        ) {
          loadHistory();
          loadDocuments(false);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }, 500);
    return () => clearInterval(id);
  }, [job, loadDocuments, loadHistory]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  const runDownload = async () => {
    setBusy(true);
    setError(null);
    try {
      const j = await client.download(selectedIds, true);
      setJob(j);
      setResult(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runProcess = async () => {
    setBusy(true);
    setError(null);
    try {
      const j = await client.process(selectedIds, settings, trials);
      setJob(j);
      setResult(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const applyManual = async (doc: DocumentPair) => {
    try {
      const pageRaw = manualPages[doc.document_id];
      const page_count = pageRaw ? Number(pageRaw) : null;
      const pdf_key = manualPdf[doc.document_id] || undefined;
      const updated = await client.manualPair({
        document_id: doc.document_id,
        pdf_key,
        page_count: page_count && !Number.isNaN(page_count) ? page_count : null,
      });
      setDocuments((docs) =>
        docs.map((d) => (d.document_id === updated.document_id ? updated : d))
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const inspectChunks = useMemo(() => {
    if (!result?.chunks?.length) return [];
    if (!inspectDoc) return result.chunks.slice(0, 20);
    return result.chunks.filter((c) => c.document_id === inspectDoc).slice(0, 40);
  }, [result, inspectDoc]);

  return (
    <div className="min-h-screen">
      {job &&
      (job.status === "running" ||
        job.status === "queued" ||
        job.status === "partial") ? (
        <div className="sticky top-0 z-50 border-b border-teal-200 bg-teal-50/95 px-4 py-3 shadow-sm backdrop-blur">
          <div className="mx-auto flex max-w-7xl flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-teal-800">
                Live job status · {job.status} · {job.stage}
              </p>
              <p className="truncate text-sm text-teal-950">{job.message}</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm font-semibold tabular-nums text-teal-900">
                {Math.round((job.progress || 0) * 100)}%
              </span>
              <a href="#run-panel" className="text-xs text-teal-800 underline">
                Jump to logs
              </a>
            </div>
          </div>
          <div className="mx-auto mt-2 h-2 max-w-7xl overflow-hidden rounded bg-teal-100">
            <div
              className="h-full bg-teal-600 transition-all"
              style={{ width: `${Math.round((job.progress || 0) * 100)}%` }}
            />
          </div>
        </div>
      ) : null}

      <header className="border-b border-slate-200/80 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-teal-700">
              DocLang · Reingestion
            </p>
            <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
              DocLang Reingestion Savings
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-600">
              Measure reuse of prepared DocLang artifacts: download → load →
              chunk → tokenize → embed. PDF extraction is out of scope; savings
              are estimated from page counts and pricing assumptions.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => loadConnection()} disabled={busy}>
              Refresh connection
            </Button>
            <Button
              variant="secondary"
              onClick={() => loadDocuments(true)}
              disabled={busy}
            >
              Refresh listing
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-6 px-4 py-6">
        {error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            {error}
          </div>
        ) : null}

        {/* Connection */}
        <Card>
          <CardHeader>
            <CardTitle>Connection</CardTitle>
            <CardDescription>
              Credentials stay server-side. Endpoint uses host:port; TLS via
              MINIO_SECURE.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-xs text-slate-500">Status</p>
              <p className="mt-1 text-sm font-medium">
                {connection?.connected ? (
                  <span className="text-teal-700">Connected</span>
                ) : (
                  <span className="text-amber-700">Not connected</span>
                )}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Bucket / prefix</p>
              <p className="mt-1 font-mono text-sm">
                {connection?.bucket || "—"}/{connection?.prefix || ""}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Endpoint host</p>
              <p className="mt-1 font-mono text-sm">
                {connection?.endpoint_host || "—"}
                {connection?.secure ? " (TLS)" : " (plain)"}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Objects</p>
              <p className="mt-1 text-sm">
                dclx {connection?.object_counts?.dclx ?? 0} · pdf{" "}
                {connection?.object_counts?.pdf ?? 0} · json{" "}
                {connection?.object_counts?.json ?? 0}
              </p>
            </div>
            {connection?.errors?.length ? (
              <div className="sm:col-span-2 lg:col-span-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <p className="font-medium">Configuration errors</p>
                <ul className="mt-1 list-disc pl-5">
                  {connection.errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
                <p className="mt-2 text-xs">
                  Copy <code>backend/.env.example</code> to{" "}
                  <code>backend/.env</code> and set real MinIO values.
                </p>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-5">
          {/* Documents */}
          <Card className="lg:col-span-3">
            <CardHeader>
              <CardTitle>Prepared documents</CardTitle>
              <CardDescription>
                {settings.source === "local"
                  ? "Local DocLang files under cache / LOCAL_DOCS_DIR. No MinIO calls."
                  : "Select DocLang archives under your prefix (e.g. docs/dclx/). PDFs are for page counting only."}
                {localRoots.length ? (
                  <span className="mt-1 block font-mono text-[11px] text-slate-400">
                    {localRoots.join(" · ")}
                  </span>
                ) : null}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {!documents.length ? (
                <p className="text-sm text-slate-500">
                  No DocLang objects listed yet. Connect MinIO and refresh.
                </p>
              ) : (
                <div className="max-h-[420px] space-y-2 overflow-auto pr-1">
                  {documents.map((doc) => (
                    <div
                      key={doc.document_id}
                      className="rounded-lg border border-slate-200 p-3 hover:border-teal-300"
                    >
                      <div className="flex items-start gap-3">
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={selected.has(doc.document_id)}
                          onChange={() => toggle(doc.document_id)}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="truncate font-medium text-slate-900">
                              {doc.document_name}
                            </p>
                            <Badge>{doc.pairing_status}</Badge>
                            {doc.cached_dclx ? (
                              <Badge className="border-teal-200 bg-teal-50 text-teal-800">
                                cached
                              </Badge>
                            ) : null}
                          </div>
                          <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
                            {doc.dclx?.key} · {formatInt(doc.dclx?.size)} bytes
                          </p>
                          <p className="mt-1 text-xs text-slate-600">
                            PDF: {doc.pdf?.key || "none"} · pages:{" "}
                            {doc.page_count ?? "—"} (
                            {doc.page_count_source || "unavailable"})
                          </p>
                          {doc.ambiguous_pdf_keys?.length ? (
                            <div className="mt-2 space-y-2 rounded-md bg-amber-50 p-2 text-xs">
                              <p className="font-medium text-amber-900">
                                Ambiguous PDF matches — choose manually
                              </p>
                              <select
                                className="w-full rounded border border-amber-200 bg-white px-2 py-1"
                                value={manualPdf[doc.document_id] || ""}
                                onChange={(e) =>
                                  setManualPdf((m) => ({
                                    ...m,
                                    [doc.document_id]: e.target.value,
                                  }))
                                }
                              >
                                <option value="">Select PDF key…</option>
                                {doc.ambiguous_pdf_keys.map((k) => (
                                  <option key={k} value={k}>
                                    {k}
                                  </option>
                                ))}
                              </select>
                              <div className="flex gap-2">
                                <Input
                                  placeholder="Manual page count"
                                  value={manualPages[doc.document_id] || ""}
                                  onChange={(e) =>
                                    setManualPages((m) => ({
                                      ...m,
                                      [doc.document_id]: e.target.value,
                                    }))
                                  }
                                />
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => applyManual(doc)}
                                >
                                  Apply
                                </Button>
                              </div>
                            </div>
                          ) : (
                            <div className="mt-2 flex gap-2">
                              <Input
                                placeholder="Override pages (manual)"
                                value={manualPages[doc.document_id] || ""}
                                onChange={(e) =>
                                  setManualPages((m) => ({
                                    ...m,
                                    [doc.document_id]: e.target.value,
                                  }))
                                }
                              />
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => applyManual(doc)}
                              >
                                Set pages
                              </Button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Settings */}
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Settings</CardTitle>
              <CardDescription>
                Chunking, embeddings, and pricing assumptions (user inputs).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Chunk size</Label>
                  <Input
                    type="number"
                    value={settings.chunk_size}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        chunk_size: Number(e.target.value),
                      }))
                    }
                  />
                </div>
                <div>
                  <Label>Overlap</Label>
                  <Input
                    type="number"
                    value={settings.chunk_overlap}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        chunk_overlap: Number(e.target.value),
                      }))
                    }
                  />
                </div>
              </div>
              <p className="text-xs text-slate-500">
                Characters. Overlap repeats text between neighbouring chunks, so
                it adds embedding tokens. Must be at most half the chunk size.
              </p>

              <div>
                <Label>Run purpose</Label>
                <select
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                  value={settings.skip_embedding ? "cost_only" : "full_embed"}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      skip_embedding: e.target.value === "cost_only",
                    }))
                  }
                >
                  <option value="cost_only">
                    Cost estimate only (skip embedding)
                  </option>
                  <option value="full_embed">
                    Full run (generate local embeddings)
                  </option>
                </select>
                <p className="mt-1 text-[11px] text-slate-500">
                  Cost-only: load → chunk → count tokens → compare extraction vs
                  embedding $. Much faster.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Embed batch size</Label>
                  <Input
                    type="number"
                    disabled={settings.skip_embedding}
                    value={settings.embedding_batch_size}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        embedding_batch_size: Number(e.target.value),
                      }))
                    }
                  />
                </div>
                <div>
                  <Label>Trials (median)</Label>
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    value={trials}
                    onChange={(e) => setTrials(Number(e.target.value))}
                  />
                </div>
              </div>

            <div>
              <Label>Document source</Label>
              <select
                className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                value={settings.source}
                onChange={(e) => {
                  const source = e.target.value as ProcessSettings["source"];
                  setSettings((s) => ({
                    ...s,
                    source,
                    run_mode:
                      source === "local" ? "staged" : s.run_mode,
                  }));
                  void loadDocuments(true, source);
                }}
              >
                <option value="local">Local disk (no MinIO)</option>
                <option value="minio">MinIO / object storage</option>
              </select>
              {settings.source === "local" ? (
                <p className="mt-1 text-[11px] text-slate-500">
                  Scans cache + LOCAL_DOCS_DIR. Already-downloaded files work
                  offline.
                </p>
              ) : null}
            </div>

              <div>
                <Label>Run mode</Label>
                <select
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                  value={settings.run_mode}
                  disabled={settings.source === "local"}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      run_mode: e.target.value as ProcessSettings["run_mode"],
                    }))
                  }
                >
                  <option value="staged">Staged (cache → process)</option>
                  <option value="download_inclusive">
                    Download-inclusive timing
                  </option>
                </select>
              </div>

              <div>
                <Label>Paid token-count method</Label>
                <select
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                  value={settings.cost.paid_token_method}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      cost: {
                        ...s.cost,
                        paid_token_method: e.target
                          .value as ProcessSettings["cost"]["paid_token_method"],
                      },
                    }))
                  }
                >
                  <option value="tiktoken_cl100k">tiktoken cl100k_base</option>
                  <option value="tiktoken_o200k">tiktoken o200k_base</option>
                  <option value="chars_div_4">approx chars/4 (proxy)</option>
                  <option value="same_as_local">
                    same as local Qwen (proxy)
                  </option>
                </select>
                <p className="mt-1 text-[11px] text-slate-500">
                  Pricing assumptions are edited in the Results section below.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Run */}
        <Card id="run-panel">
          <CardHeader>
            <CardTitle>Run</CardTitle>
            <CardDescription>
              No PDF extraction or DocLang preparation actions. Jobs run
              sequentially.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={runDownload}
                disabled={
                  busy || !selectedIds.length || settings.source === "local"
                }
                variant="secondary"
              >
                Download selected artifacts
              </Button>
              <Button
                onClick={runProcess}
                disabled={busy || !selectedIds.length}
              >
                {settings.skip_embedding
                  ? "Estimate cost (no embedding)"
                  : "Process prepared DocLang"}
              </Button>
              <Button
                variant="outline"
                onClick={runProcess}
                disabled={busy || !selectedIds.length}
              >
                Repeat
              </Button>
            </div>
            {job ? (
              <div className="rounded-lg border border-teal-200 bg-teal-50/40 p-4 ring-1 ring-teal-100">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    Job {job.job_id.slice(0, 8)}… ·{" "}
                    <span className="uppercase text-teal-800">{job.status}</span>{" "}
                    · stage <code>{job.stage}</code>
                  </p>
                  <p className="text-sm font-semibold tabular-nums text-teal-900">
                    {Math.round((job.progress || 0) * 100)}%
                  </p>
                </div>
                <p className="mt-1 text-sm text-slate-700">{job.message}</p>
                <div className="mt-2 h-2.5 overflow-hidden rounded bg-slate-200">
                  <div
                    className="h-full bg-teal-600 transition-all"
                    style={{ width: `${Math.round((job.progress || 0) * 100)}%` }}
                  />
                </div>
                <pre className="mt-3 max-h-56 overflow-auto rounded bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
                  {(job.logs || []).slice(-60).join("\n") || "Waiting for logs…"}
                </pre>
                {job.status === "running" && job.stage === "embed" ? (
                  <p className="mt-2 text-xs text-amber-800">
                    Embedding can take many minutes on CPU for large filings
                    (this one has ~1800+ chunks). Watch the log lines for
                    percent complete and ETA.
                  </p>
                ) : null}
                {job.error ? (
                  <p className="mt-2 text-sm text-rose-700">{job.error}</p>
                ) : null}
                {result?.partial ? (
                  <p className="mt-2 text-sm text-amber-800">
                    Partial results — not a completed clean benchmark.
                  </p>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                Select documents, download if needed, then process.
              </p>
            )}
          </CardContent>
        </Card>

        <CostComparisonResults
          result={result}
          settings={settings}
          onSettingsChange={setSettings}
        />

        {/* Inspection */}
        <Card>
          <CardHeader>
            <CardTitle>Inspection</CardTitle>
            <CardDescription>
              Exact chunk text submitted to the embedding model, with per-chunk
              tokens.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {!result ? (
              <p className="text-sm text-slate-500">No inspection data yet.</p>
            ) : (
              <>
                <div className="flex flex-wrap gap-2">
                  <select
                    className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                    value={inspectDoc}
                    onChange={(e) => setInspectDoc(e.target.value)}
                  >
                    <option value="">All documents</option>
                    {result.documents.map((d) => (
                      <option key={d.document_id} value={d.document_id}>
                        {d.document_name}
                      </option>
                    ))}
                  </select>
                </div>
                {inspectDoc && result.preview_markdown?.[inspectDoc] ? (
                  <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-3 text-xs whitespace-pre-wrap">
                    {result.preview_markdown[inspectDoc]}
                  </pre>
                ) : null}
                <div className="max-h-96 space-y-2 overflow-auto">
                  {inspectChunks.map((ch) => (
                    <div
                      key={ch.chunk_id}
                      className="rounded-lg border border-slate-200 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <span className="font-mono">{ch.chunk_id}</span>
                        <Badge>local {ch.local_token_count}</Badge>
                        <Badge>paid est. {ch.paid_token_count ?? "—"}</Badge>
                        {ch.truncated ? (
                          <Badge className="border-rose-200 bg-rose-50 text-rose-800">
                            truncated risk
                          </Badge>
                        ) : null}
                      </div>
                      <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-800">
                        {ch.text}
                      </pre>
                      {ch.truncation_note ? (
                        <p className="mt-1 text-xs text-rose-700">
                          {ch.truncation_note}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
                <div className="text-xs text-slate-500">
                  Source keys:{" "}
                  {result.documents
                    .map((d) => d.dclx?.key)
                    .filter(Boolean)
                    .join(", ") || "—"}
                </div>
                {result.cache_hits?.length || result.cache_misses?.length ? (
                  <p className="text-xs text-slate-500">
                    Cache hits: {result.cache_hits.length} · misses:{" "}
                    {result.cache_misses.length}
                  </p>
                ) : null}
              </>
            )}
          </CardContent>
        </Card>

        {/* History */}
        <Card>
          <CardHeader>
            <CardTitle>History</CardTitle>
            <CardDescription>
              Runs saved under backend/data/runs/.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!history.length ? (
              <p className="text-sm text-slate-500">No saved runs yet.</p>
            ) : (
              <div className="overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">Job</th>
                      <th className="py-2 pr-3">Status</th>
                      <th className="py-2 pr-3">Pages</th>
                      <th className="py-2 pr-3">Tokens</th>
                      <th className="py-2 pr-3">Process s</th>
                      <th className="py-2">Export</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.slice(0, 20).map((r) => (
                      <tr key={r.job_id} className="border-t border-slate-100">
                        <td className="py-2 pr-3 font-mono text-xs">
                          {r.job_id.slice(0, 8)}
                        </td>
                        <td className="py-2 pr-3">
                          {r.status}
                          {r.partial ? " (partial)" : ""}
                        </td>
                        <td className="py-2 pr-3">{r.pages_total}</td>
                        <td className="py-2 pr-3">{r.local_tokens_total}</td>
                        <td className="py-2 pr-3">
                          {formatSeconds(
                            r.stage_timings?.find(
                              (t) => t.name === "process_excluding_download"
                            )?.seconds
                          )}
                        </td>
                        <td className="py-2">
                          <button
                            className="text-teal-700 hover:underline"
                            onClick={() => setResult(r)}
                          >
                            Load
                          </button>{" "}
                          ·{" "}
                          <a
                            className="text-teal-700 hover:underline"
                            href={client.exportUrl(r.job_id, "csv")}
                          >
                            CSV
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
