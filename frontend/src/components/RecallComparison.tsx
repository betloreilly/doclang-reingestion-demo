"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  client,
  type RecallJob,
  type RecallPipelineKey,
  type RecallQuestion,
  type RecallRetrieval,
  type RecallRun,
  type RecallStatus,
} from "@/lib/api";
import { formatInt, formatUsd } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge, Input, Label } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type Scope = "global" | "per_document";
type QuestionFilter = "all" | "doclang_only" | "unstructured_only" | "both" | "both_missed";

const PIPELINES: RecallPipelineKey[] = ["doclang", "unstructured"];
const TONES: Record<
  RecallPipelineKey,
  { bar: string; text: string; card: string; name: string }
> = {
  doclang: {
    bar: "bg-teal-600",
    text: "text-teal-800",
    card: "border-teal-200 bg-gradient-to-br from-teal-50 to-white",
    name: "DocLang",
  },
  unstructured: {
    bar: "bg-amber-500",
    text: "text-amber-800",
    card: "border-amber-200 bg-gradient-to-br from-amber-50 to-white",
    name: "Unstructured",
  },
};
const TYPE_LABEL: Record<string, string> = {
  "domain-relevant": "Domain questions",
  "metrics-generated": "Metrics questions",
  "novel-generated": "Novel questions",
};

const pct = (v?: number | null) =>
  v === undefined || v === null || Number.isNaN(v)
    ? "—"
    : `${(v * 100).toFixed(1)}%`;

function deltaPp(a?: number, b?: number): string {
  if (a === undefined || b === undefined) return "—";
  const d = (a - b) * 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(1)} pp`;
}

function found(rank: number | null | undefined, k: number) {
  return rank !== null && rank !== undefined && rank <= k;
}

export function RecallComparison({
  chunkSize,
  chunkOverlap,
}: {
  chunkSize: number;
  chunkOverlap: number;
}) {
  const [status, setStatus] = useState<RecallStatus | null>(null);
  const [runs, setRuns] = useState<RecallRun[]>([]);
  const [run, setRun] = useState<RecallRun | null>(null);
  const [job, setJob] = useState<RecallJob | null>(null);
  const [ingestJob, setIngestJob] = useState<RecallJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [maxDocs, setMaxDocs] = useState<string>("");
  const [scope, setScope] = useState<Scope>("global");
  const [k, setK] = useState<number>(5);
  const [filter, setFilter] = useState<QuestionFilter>("doclang_only");
  const [openQuestion, setOpenQuestion] = useState<string | null>(null);
  const [osBusy, setOsBusy] = useState(false);

  const loadRun = useCallback(async (runId: string) => {
    try {
      setRun(await client.recallRun(runId));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([
        client.recallStatus(),
        client.recallRuns(),
      ]);
      setStatus(s);
      setRuns(r.runs);
      return r.runs;
    } catch (e) {
      setError((e as Error).message);
      return [];
    }
  }, []);

  useEffect(() => {
    refresh().then((list) => {
      const preferred =
        list.find((r) => r.retrieval_backend === "opensearch") ??
        list.find((r) => !r.estimate_only) ??
        list[0];
      if (preferred) loadRun(preferred.run_id);
    });
  }, [refresh, loadRun]);

  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed") return;
    const timer = setInterval(async () => {
      try {
        const next = await client.recallJob(job.job_id);
        setJob(next);
        if (next.status === "completed" && next.run_id) {
          await refresh();
          await loadRun(next.run_id);
        }
        if (next.status === "failed") setError(next.error ?? "Recall run failed");
      } catch (e) {
        setError((e as Error).message);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [job, refresh, loadRun]);

  useEffect(() => {
    if (
      !ingestJob ||
      ingestJob.status === "completed" ||
      ingestJob.status === "failed"
    )
      return;
    const timer = setInterval(async () => {
      try {
        const next = await client.opensearchIngestJob(ingestJob.job_id);
        setIngestJob(next);
        if (next.status === "completed") await refresh();
        if (next.status === "failed")
          setError(next.error ?? "OpenSearch ingest failed");
      } catch (e) {
        setError((e as Error).message);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [ingestJob, refresh]);

  const start = async (estimateOnly: boolean) => {
    setError(null);
    try {
      const parsed = maxDocs.trim() ? Number(maxDocs) : null;
      setJob(
        await client.startRecall({
          estimate_only: estimateOnly,
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
          max_documents: parsed && parsed > 0 ? parsed : null,
        })
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const startIngest = async (recreate = false) => {
    setError(null);
    try {
      const parsed = maxDocs.trim() ? Number(maxDocs) : null;
      setIngestJob(
        await client.startOpensearchIngest({
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
          recreate_indexes: recreate,
          max_documents: parsed && parsed > 0 ? parsed : null,
        })
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const runOpensearchEval = async () => {
    setError(null);
    setOsBusy(true);
    try {
      const parsed = maxDocs.trim() ? Number(maxDocs) : null;
      const next = await client.evaluateOpensearch(
        parsed && parsed > 0 ? parsed : null
      );
      await refresh();
      await loadRun(next.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setOsBusy(false);
    }
  };

  const running =
    job !== null && (job.status === "queued" || job.status === "running");
  const ingesting =
    ingestJob !== null &&
    (ingestJob.status === "queued" || ingestJob.status === "running");
  const os = status?.opensearch;
  const osReady =
    Boolean(status?.opensearch_ready) ||
    (Boolean(os?.connected) &&
      (os?.indexes?.doclang?.docs ?? 0) > 0 &&
      (os?.indexes?.unstructured?.docs ?? 0) > 0);
  const embeddingsReady = Boolean(status?.embeddings_cached?.ready);
  const needsEmbedding =
    !embeddingsReady && !osReady && Boolean(status?.openai_configured);

  const retrieval = (p: RecallPipelineKey): RecallRetrieval | undefined =>
    run?.pipelines[p].retrieval?.[scope];
  const hasRetrieval = Boolean(
    run && !run.estimate_only && run.pipelines.doclang.retrieval
  );
  const latestEstimate = runs.find((r) => r.estimate_only);
  const remainingCost =
    run?.embedding.estimated_cost_remaining_usd ??
    latestEstimate?.embedding.estimated_cost_remaining_usd ??
    null;

  const winCounts = useMemo(() => {
    if (!run?.questions || !hasRetrieval) return null;
    const rankKey =
      scope === "global" ? "global_first_rank" : "per_document_first_rank";
    let doclangOnly = 0;
    let unstructuredOnly = 0;
    let both = 0;
    let bothMissed = 0;
    for (const q of run.questions) {
      const d = found(q.doclang[rankKey], k);
      const u = found(q.unstructured[rankKey], k);
      if (d && u) both++;
      else if (d && !u) doclangOnly++;
      else if (u && !d) unstructuredOnly++;
      else bothMissed++;
    }
    return { doclangOnly, unstructuredOnly, both, bothMissed };
  }, [run, hasRetrieval, scope, k]);

  const questions = useMemo(() => {
    if (!run?.questions || !hasRetrieval) return [];
    const rankKey =
      scope === "global" ? "global_first_rank" : "per_document_first_rank";
    return run.questions.filter((q) => {
      const d = found(q.doclang[rankKey], k);
      const u = found(q.unstructured[rankKey], k);
      if (filter === "doclang_only") return d && !u;
      if (filter === "unstructured_only") return u && !d;
      if (filter === "both") return d && u;
      if (filter === "both_missed") return !d && !u;
      return true;
    });
  }, [run, hasRetrieval, scope, k, filter]);

  const dHit = retrieval("doclang")?.overall.hit_at[String(k)];
  const uHit = retrieval("unstructured")?.overall.hit_at[String(k)];
  const setupStep = osReady ? 3 : embeddingsReady ? 2 : 1;
  const busy = running || ingesting || osBusy;
  const nQuestions = retrieval("doclang")?.overall.n ?? run?.dataset.questions_evaluated;
  const doclangAhead =
    dHit !== undefined && uHit !== undefined ? dHit >= uHit : null;

  return (
    <div id="recall-comparison" className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="font-display text-2xl tracking-tight">
            Retrieval quality
          </CardTitle>
          <CardDescription className="max-w-3xl text-sm leading-relaxed text-slate-600">
            We ask the same FinanceBench questions twice: once over DocLang text,
            once over Unstructured text. Chunking and embeddings stay the same.
            The question is simple:{" "}
            <span className="font-medium text-slate-800">
              does search return the PDF page that holds the answer?
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4 text-sm leading-relaxed text-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Example
            </p>
            <p className="mt-2">
              <span className="font-medium text-slate-900">
                “What is FY2018 capital expenditure for 3M?”
              </span>{" "}
              FinanceBench says the answer is on page{" "}
              <span className="font-medium text-slate-900">60</span> of{" "}
              <span className="font-mono text-xs">3M_2018_10K</span>. If page 60
              shows up in the top {k} search hits → that pipeline{" "}
              <span className="font-medium text-teal-800">found it</span>. If not
              → it <span className="font-medium text-rose-700">missed</span>.
            </p>
          </div>

          <ol className="grid gap-3 sm:grid-cols-3">
            <SetupStep
              n={1}
              title="Prepare embeddings"
              done={embeddingsReady || osReady}
              active={setupStep === 1}
              detail={
                embeddingsReady || osReady
                  ? "Ready on disk — no new OpenAI call"
                  : "One-time embed of chunks + questions"
              }
            />
            <SetupStep
              n={2}
              title="Load into OpenSearch"
              done={osReady}
              active={setupStep === 2}
              detail={
                osReady
                  ? `${formatInt(os?.indexes?.doclang?.docs)} + ${formatInt(os?.indexes?.unstructured?.docs)} chunks indexed`
                  : os?.connected
                    ? "OpenSearch is up — load vectors next"
                    : "Start with: docker compose up -d"
              }
            />
            <SetupStep
              n={3}
              title="Compare scores"
              done={hasRetrieval}
              active={setupStep === 3}
              detail="See who finds the evidence page more often"
            />
          </ol>

          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Next step
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              {osReady ? (
                <Button disabled={busy} onClick={runOpensearchEval}>
                  {osBusy ? "Scoring…" : "Score with OpenSearch"}
                </Button>
              ) : embeddingsReady && os?.connected ? (
                <Button disabled={busy} onClick={() => startIngest(false)}>
                  {ingesting ? "Loading…" : "Load vectors into OpenSearch"}
                </Button>
              ) : (
                <Button
                  disabled={busy || !status?.openai_configured}
                  onClick={() => start(false)}
                >
                  {running
                    ? "Embedding…"
                    : `Create embeddings${
                        remainingCost != null
                          ? ` (≈ ${formatUsd(remainingCost)})`
                          : ""
                      }`}
                </Button>
              )}
              <p className="max-w-lg text-sm text-slate-600">
                {osReady
                  ? "Indexes are ready. This only searches — it does not embed again."
                  : embeddingsReady && os?.connected
                    ? "Embeddings are ready. Load them once, then score."
                    : needsEmbedding
                      ? "First time: embed DocLang and Unstructured chunks (then reuse)."
                      : "Add OPENAI_API_KEY in backend/.env, or restore the embedding cache."}
              </p>
            </div>
          </div>

          {(running && job) || (ingesting && ingestJob) ? (
            <div className="space-y-1">
              <div className="h-2 w-full overflow-hidden rounded bg-slate-100">
                <div
                  className="h-2 bg-teal-600 transition-all"
                  style={{
                    width: `${Math.round((ingesting ? ingestJob!.progress : job!.progress) * 100)}%`,
                  }}
                />
              </div>
              <p className="text-xs text-slate-600">
                {ingesting ? ingestJob!.message : job!.message}
              </p>
            </div>
          ) : null}
          {error ? <p className="text-sm text-rose-700">{error}</p> : null}

          <details className="group rounded-lg border border-slate-200 bg-white">
            <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-slate-700 [&::-webkit-details-marker]:hidden">
              Advanced options
              <span className="ml-2 font-normal text-slate-400 group-open:hidden">
                show
              </span>
              <span className="ml-2 hidden font-normal text-slate-400 group-open:inline">
                hide
              </span>
            </summary>
            <div className="space-y-3 border-t border-slate-100 px-4 py-3">
              <div className="flex flex-wrap items-end gap-3">
                <div className="w-36">
                  <Label htmlFor="recall-max-docs">Limit docs</Label>
                  <Input
                    id="recall-max-docs"
                    type="number"
                    min={1}
                    max={84}
                    placeholder="all 84"
                    value={maxDocs}
                    onChange={(e) => setMaxDocs(e.target.value)}
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => start(true)}
                >
                  Estimate tokens only
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    busy || (needsEmbedding && !status?.openai_configured)
                  }
                  onClick={() => start(false)}
                >
                  Score in memory
                </Button>
                {osReady ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy || !os?.connected}
                    onClick={() => startIngest(true)}
                  >
                    Rebuild OpenSearch indexes
                  </Button>
                ) : null}
              </div>
              <p className="text-xs text-slate-500">
                Model {status?.embedding_model ?? "text-embedding-3-large"} ·
                chunk {chunkSize}/{chunkOverlap} · OpenSearch{" "}
                {os?.connected ? os.version ?? "up" : "offline"} · Unstructured
                PDFs cached: {formatInt(status?.unstructured_cached_documents)}
              </p>
            </div>
          </details>
        </CardContent>
      </Card>

      {hasRetrieval && run ? (
        <Card>
          <CardHeader className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle>Who finds the right page?</CardTitle>
                <CardDescription className="mt-1 max-w-2xl text-sm leading-relaxed">
                  DocLang vs Unstructured on the same FinanceBench questions.
                  Pick a search range and a top-k below, then read the %.
                  {run.retrieval_backend === "opensearch"
                    ? " Scored with OpenSearch."
                    : " Scored in memory."}{" "}
                  {formatInt(nQuestions)} questions ·{" "}
                  {formatInt(run.dataset.documents_evaluated)} filings.
                </CardDescription>
              </div>
              {runs.length > 0 ? (
                <select
                  className="h-9 max-w-xs rounded-md border border-slate-300 bg-white px-2 text-xs"
                  value={run.run_id}
                  onChange={(e) => loadRun(e.target.value)}
                  aria-label="Choose a past run"
                >
                  {runs
                    .filter((r) => !r.estimate_only)
                    .map((r) => (
                      <option key={r.run_id} value={r.run_id}>
                        {new Date(r.created_at).toLocaleString()} ·{" "}
                        {r.retrieval_backend === "opensearch"
                          ? "OpenSearch"
                          : "in-memory"}{" "}
                        · {r.dataset.questions_evaluated} q
                      </option>
                    ))}
                </select>
              ) : null}
            </div>

            <div className="space-y-4 rounded-xl border border-slate-200 bg-slate-50/60 p-4">
              <div>
                <p className="text-sm font-semibold text-slate-900">
                  1. Where should search look?
                </p>
                <p className="mt-1 text-xs leading-relaxed text-slate-600">
                  Imagine the question: “What is FY2018 capital expenditure for
                  3M?” FinanceBench says the answer is on{" "}
                  <span className="font-medium text-slate-800">
                    page 60 of 3M_2018_10K
                  </span>
                  . The two modes below change how wide the search is.
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <ScopeChoice
                  selected={scope === "global"}
                  badge="Harder · lower %"
                  title="All filings together"
                  detail="Search every chunk from all 84 PDFs. Wrong companies (Boeing, Amcor, …) can rank above the real 3M page. Success = find the right filing and the right page."
                  onClick={() => setScope("global")}
                />
                <ScopeChoice
                  selected={scope === "per_document"}
                  badge="Easier · higher %"
                  title="Only that one filing"
                  detail="Search only inside 3M_2018_10K. Other companies are excluded. Success = find page 60 inside that file. Scores rise because the hard “which document?” step is skipped."
                  onClick={() => setScope("per_document")}
                />
              </div>

              <div>
                <p className="text-sm font-semibold text-slate-900">
                  2. How close does the right page need to be?
                </p>
                <p className="mt-1 text-xs leading-relaxed text-slate-600">
                  Search returns a ranked list of pages (each page counted once,
                  by its best chunk). We check whether page 60 appears near the
                  top.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(run.settings.ks?.length
                    ? run.settings.ks
                    : [1, 3, 5, 10]
                  ).map((kk) => (
                    <Button
                      key={kk}
                      size="sm"
                      variant={k === kk ? "secondary" : "outline"}
                      onClick={() => setK(kk)}
                    >
                      Top {kk}
                    </Button>
                  ))}
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div
                    className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${
                      k === 1
                        ? "border-amber-200 bg-amber-50 text-amber-950"
                        : "border-slate-200 bg-white text-slate-600"
                    }`}
                  >
                    <p className="font-semibold">Top 1</p>
                    <p className="mt-0.5">
                      Only the #1 hit counts. Often a related but wrong page —
                      expect a low %.
                    </p>
                  </div>
                  <div
                    className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${
                      k >= 5
                        ? "border-teal-200 bg-teal-50 text-teal-950"
                        : "border-slate-200 bg-white text-slate-600"
                    }`}
                  >
                    <p className="font-semibold">Top 5 (recommended)</p>
                    <p className="mt-0.5">
                      Page 60 can be anywhere in the first 5 pages. Fairer “did
                      search get us close?” check.
                    </p>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-teal-200 bg-white px-3 py-2.5 text-sm leading-relaxed text-slate-800">
                <span className="font-semibold text-teal-900">
                  You are measuring now:{" "}
                </span>
                {scope === "global"
                  ? `among all 84 filings, how often is the evidence page in the top ${k} hits?`
                  : `inside the known filing only, how often is the evidence page in the top ${k} hits?`}{" "}
                Compare DocLang vs Unstructured on this same setting.
              </div>
            </div>
          </CardHeader>

          <CardContent className="space-y-8">
            <div className="grid gap-4 md:grid-cols-3">
              {PIPELINES.map((p) => (
                <div key={p} className={`rounded-xl border p-5 ${TONES[p].card}`}>
                  <p className={`text-sm font-semibold ${TONES[p].text}`}>
                    {TONES[p].name}
                  </p>
                  <p className="mt-2 font-display text-4xl font-semibold tabular-nums text-slate-900">
                    {pct(retrieval(p)?.overall.hit_at[String(k)])}
                  </p>
                  <p className="mt-2 text-xs leading-relaxed text-slate-600">
                    found the evidence page in the top {k}
                  </p>
                </div>
              ))}
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-5">
                <p className="text-sm font-semibold text-slate-800">
                  Gap (DocLang − Unstructured)
                </p>
                <p
                  className={`mt-2 font-display text-4xl font-semibold tabular-nums ${
                    doclangAhead ? "text-teal-800" : "text-rose-700"
                  }`}
                >
                  {deltaPp(dHit, uHit)}
                </p>
                <p className="mt-2 text-xs leading-relaxed text-slate-600">
                  {doclangAhead
                    ? "DocLang finds the right page more often on this setup."
                    : "Unstructured finds the right page more often on this setup."}
                </p>
              </div>
            </div>

            {winCounts ? (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <CountChip
                  label="Both found it"
                  value={winCounts.both}
                  tone="neutral"
                />
                <CountChip
                  label="Only DocLang"
                  value={winCounts.doclangOnly}
                  tone="teal"
                />
                <CountChip
                  label="Only Unstructured"
                  value={winCounts.unstructuredOnly}
                  tone="amber"
                />
                <CountChip
                  label="Both missed"
                  value={winCounts.bothMissed}
                  tone="rose"
                />
              </div>
            ) : null}

            <div className="grid gap-8 lg:grid-cols-2">
              <div>
                <p className="mb-1 text-sm font-semibold text-slate-900">
                  As we look at more hits
                </p>
                <p className="mb-3 text-xs text-slate-500">
                  Teal = DocLang · Amber = Unstructured. Higher is better.
                </p>
                <div className="space-y-3">
                  {(run.settings.ks?.length
                    ? run.settings.ks
                    : [1, 3, 5, 10]
                  ).map((kk) => (
                    <div key={kk}>
                      <p className="text-xs text-slate-500">Top {kk}</p>
                      {PIPELINES.map((p) => {
                        const v =
                          retrieval(p)?.overall.hit_at[String(kk)] ?? 0;
                        return (
                          <div key={p} className="mt-1 flex items-center gap-2">
                            <div className="h-2.5 flex-1 rounded bg-slate-100">
                              <div
                                className={`h-2.5 rounded ${TONES[p].bar}`}
                                style={{ width: `${v * 100}%` }}
                              />
                            </div>
                            <span
                              className={`w-14 text-right text-xs tabular-nums ${TONES[p].text}`}
                            >
                              {pct(v)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-1 text-sm font-semibold text-slate-900">
                  By question type (top {k})
                </p>
                <p className="mb-3 text-xs text-slate-500">
                  About 50 questions in each FinanceBench type
                </p>
                <div className="space-y-3">
                  {Object.keys(retrieval("doclang")?.by_type ?? {}).map((t) => (
                    <div key={t}>
                      <p className="text-xs text-slate-500">
                        {TYPE_LABEL[t] ?? t}
                      </p>
                      {PIPELINES.map((p) => {
                        const v =
                          retrieval(p)?.by_type[t]?.hit_at[String(k)] ?? 0;
                        return (
                          <div key={p} className="mt-1 flex items-center gap-2">
                            <div className="h-2.5 flex-1 rounded bg-slate-100">
                              <div
                                className={`h-2.5 rounded ${TONES[p].bar}`}
                                style={{ width: `${v * 100}%` }}
                              />
                            </div>
                            <span
                              className={`w-14 text-right text-xs tabular-nums ${TONES[p].text}`}
                            >
                              {pct(v)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <section className="rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-4 py-3">
                <p className="text-sm font-semibold text-slate-900">
                  Look at individual questions
                </p>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                  Click a question to see the top pages each pipeline returned.
                  Green highlight = evidence page.
                </p>
              </div>
              <div className="space-y-3 px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  {(
                    [
                      ["doclang_only", "Only DocLang", winCounts?.doclangOnly],
                      [
                        "unstructured_only",
                        "Only Unstructured",
                        winCounts?.unstructuredOnly,
                      ],
                      ["both", "Both found", winCounts?.both],
                      ["both_missed", "Both missed", winCounts?.bothMissed],
                      ["all", "All questions", nQuestions],
                    ] as [QuestionFilter, string, number | undefined][]
                  ).map(([f, label, count]) => (
                    <Button
                      key={f}
                      size="sm"
                      variant={filter === f ? "secondary" : "ghost"}
                      onClick={() => setFilter(f)}
                    >
                      {label}
                      {count != null ? ` (${count})` : ""}
                    </Button>
                  ))}
                </div>
                <div className="max-h-[480px] space-y-2 overflow-y-auto pr-1">
                  {questions.length === 0 ? (
                    <p className="py-4 text-center text-xs text-slate-500">
                      No questions in this filter for top {k}.
                    </p>
                  ) : (
                    questions.map((q) => (
                      <QuestionRow
                        key={q.financebench_id}
                        q={q}
                        scope={scope}
                        k={k}
                        open={openQuestion === q.financebench_id}
                        onToggle={() =>
                          setOpenQuestion(
                            openQuestion === q.financebench_id
                              ? null
                              : q.financebench_id
                          )
                        }
                      />
                    ))
                  )}
                </div>
              </div>
            </section>

            <p className="text-xs text-slate-500">
              Model {run.embedding.model}
              {run.embedding.cost_this_run_usd > 0
                ? ` · billed ${formatUsd(run.embedding.cost_this_run_usd)} this run`
                : " · $0 billed this run"}
              . A few points on 150 questions can be noise — use the explorer to
              see why.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="space-y-2 py-8 text-sm text-slate-600">
            <p className="font-medium text-slate-800">No scored run yet</p>
            <p>
              Use the button above to create embeddings, load OpenSearch, or
              score. After that you will see DocLang vs Unstructured side by
              side.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ScopeChoice({
  selected,
  badge,
  title,
  detail,
  onClick,
}: {
  selected: boolean;
  badge: string;
  title: string;
  detail: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-xl border p-4 text-left transition ${
        selected
          ? "border-teal-300 bg-white ring-1 ring-teal-200"
          : "border-slate-200 bg-white/70 hover:border-slate-300"
      }`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {badge}
      </p>
      <p className="mt-1 text-sm font-semibold text-slate-900">{title}</p>
      <p className="mt-1.5 text-xs leading-relaxed text-slate-600">{detail}</p>
    </button>
  );
}

function CountChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "teal" | "amber" | "rose" | "neutral";
}) {
  const styles = {
    teal: "border-teal-200 bg-teal-50 text-teal-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    rose: "border-rose-200 bg-rose-50 text-rose-900",
    neutral: "border-slate-200 bg-slate-50 text-slate-800",
  }[tone];
  return (
    <div className={`rounded-lg border px-3 py-2 ${styles}`}>
      <p className="text-[11px] font-medium opacity-80">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function SetupStep({
  n,
  title,
  detail,
  done,
  active,
}: {
  n: number;
  title: string;
  detail: string;
  done: boolean;
  active: boolean;
}) {
  return (
    <li
      className={`rounded-xl border px-4 py-3 ${
        done
          ? "border-teal-200 bg-teal-50/60"
          : active
            ? "border-slate-300 bg-white"
            : "border-slate-200 bg-slate-50/50"
      }`}
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Step {n}
        {done ? " · done" : active ? " · next" : ""}
      </p>
      <p className="mt-1 text-sm font-semibold text-slate-900">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-slate-600">{detail}</p>
    </li>
  );
}

function QuestionRow({
  q,
  scope,
  k,
  open,
  onToggle,
}: {
  q: RecallQuestion;
  scope: Scope;
  k: number;
  open: boolean;
  onToggle: () => void;
}) {
  const rankKey =
    scope === "global" ? "global_first_rank" : "per_document_first_rank";
  const topKey = scope === "global" ? "global_top" : "per_document_top";
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button type="button" className="w-full p-3 text-left" onClick={onToggle}>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <span className="font-mono">{q.doc_name}</span>
          <span>· evidence page {q.evidence_pages.join(", ")}</span>
          {PIPELINES.map((p) => {
            const rank = q[p][rankKey];
            const ok = found(rank, k);
            return (
              <Badge
                key={p}
                className={
                  ok
                    ? "border-teal-200 bg-teal-50 text-teal-900"
                    : "border-rose-200 bg-rose-50 text-rose-800"
                }
              >
                {TONES[p].name}: {ok ? `found (#${rank})` : "missed"}
              </Badge>
            );
          })}
        </div>
        <p className="mt-1.5 text-sm text-slate-900">{q.question}</p>
      </button>
      {open ? (
        <div className="grid gap-3 border-t border-slate-100 p-3 md:grid-cols-2">
          <p className="text-xs text-slate-600 md:col-span-2">
            <span className="font-medium">Labeled answer:</span> {q.answer}
          </p>
          {PIPELINES.map((p) => (
            <div key={p}>
              <p className={`mb-1 text-xs font-semibold ${TONES[p].text}`}>
                {TONES[p].name} — top hits
              </p>
              {(q[p][topKey] ?? []).length === 0 ? (
                <p className="text-[11px] text-slate-500">No hits stored.</p>
              ) : (
                (q[p][topKey] ?? []).map((hit, i) => (
                  <div
                    key={i}
                    className={`mb-2 rounded border p-2 text-[11px] ${
                      hit.relevant
                        ? "border-teal-300 bg-teal-50"
                        : "border-slate-200 bg-slate-50"
                    }`}
                  >
                    <p className="text-slate-500">
                      #{i + 1} · {hit.doc_name} · page {hit.page}
                      {hit.relevant ? " · evidence page ✓" : ""}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap font-mono text-slate-700">
                      {hit.snippet}
                    </p>
                  </div>
                ))
              )}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
