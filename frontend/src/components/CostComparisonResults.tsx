"use client";

import { formatInt, formatPct, formatRatio, formatSeconds, formatUsd } from "@/lib/utils";
import type { ProcessSettings, RunResult } from "@/lib/api";
import { Badge, Input, Label } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { client } from "@/lib/api";

function tokenMethodLabel(method: ProcessSettings["cost"]["paid_token_method"]): {
  label: string;
  exact: boolean;
} {
  switch (method) {
    case "tiktoken_cl100k":
      return {
        label: "tiktoken cl100k_base (OpenAI-compatible encoding)",
        exact: true,
      };
    case "tiktoken_o200k":
      return {
        label: "tiktoken o200k_base (OpenAI-compatible encoding)",
        exact: true,
      };
    case "same_as_local":
      return {
        label: "proxy: same count as local Qwen tokenizer",
        exact: false,
      };
    case "chars_div_4":
      return { label: "approximate proxy: characters ÷ 4", exact: false };
    default:
      return { label: String(method), exact: false };
  }
}

function CostHeroCard({
  title,
  amount,
  basis,
  footnote,
  accent,
}: {
  title: string;
  amount: string;
  basis: string;
  footnote?: string;
  accent: "teal" | "sky" | "slate";
}) {
  const tones = {
    teal: "border-teal-200 bg-gradient-to-br from-teal-50 to-white",
    sky: "border-sky-200 bg-gradient-to-br from-sky-50 to-white",
    slate: "border-slate-200 bg-gradient-to-br from-slate-50 to-white",
  };
  return (
    <div className={`rounded-xl border p-5 shadow-sm ${tones[accent]}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-slate-800">{title}</p>
        <Badge className="border-amber-200 bg-amber-50 text-amber-900">
          Estimated
        </Badge>
      </div>
      <p className="mt-3 font-display text-3xl font-semibold tabular-nums tracking-tight text-slate-900">
        {amount}
      </p>
      <p className="mt-2 text-xs leading-relaxed text-slate-600">{basis}</p>
      {footnote ? (
        <p className="mt-3 border-t border-slate-200/80 pt-2 text-[11px] leading-relaxed text-slate-500">
          {footnote}
        </p>
      ) : null}
    </div>
  );
}

export function CostComparisonResults({
  result,
  settings,
  onSettingsChange,
}: {
  result: RunResult | null;
  settings: ProcessSettings;
  onSettingsChange: (next: ProcessSettings) => void;
}) {
  const pages = result?.pages_total ?? 0;
  const localTokens = result?.local_tokens_total ?? 0;
  const paidTokens = result?.paid_tokens_total ?? null;
  const prepSeconds =
    result?.stage_timings?.find((t) => t.name === "process_excluding_download")
      ?.seconds ?? null;
  const embedSkipped = Boolean(result?.embedding_meta?.skipped);
  const chunkCount = result?.chunks_total ?? 0;

  const pageSources = Array.from(
    new Set(
      (result?.documents || [])
        .map((d) => d.page_count_source)
        .filter((s): s is string => Boolean(s))
    )
  );
  const pageSourceLabel = (() => {
    if (pageSources.length === 0) return "unavailable";
    if (pageSources.length === 1) {
      const s = pageSources[0];
      if (s === "pdf") return "original PDF (page count only — no re-extraction)";
      if (s === "doclang_pages") return "DocLang page metadata";
      if (s === "manual") return "manual entry";
      return s;
    }
    return pageSources.join(", ");
  })();
  const pageFromPdf = pageSources.length > 0 && pageSources.every((s) => s === "pdf");

  const extractionRate = settings.cost.extraction_price_per_1000_pages;
  const embeddingRate = settings.cost.embedding_price_per_million_tokens;
  const extractionProvider =
    settings.cost.extraction_provider || "Docling SaaS";
  const embeddingModel =
    settings.cost.paid_embedding_model || "paid embedding model";

  const extractionCost =
    result != null ? (pages / 1000) * extractionRate : null;
  const embeddingCost =
    result != null &&
    paidTokens != null &&
    embeddingRate != null &&
    !Number.isNaN(embeddingRate)
      ? (paidTokens / 1_000_000) * embeddingRate
      : null;
  const totalCost =
    extractionCost != null && embeddingCost != null
      ? extractionCost + embeddingCost
      : null;

  const ratio =
    extractionCost != null && embeddingCost != null && embeddingCost > 0
      ? extractionCost / embeddingCost
      : null;
  const extractionPctOfTotal =
    totalCost != null && totalCost > 0 && extractionCost != null
      ? (extractionCost / totalCost) * 100
      : null;
  const embeddingPctOfTotal =
    totalCost != null && totalCost > 0 && embeddingCost != null
      ? (embeddingCost / totalCost) * 100
      : null;

  // Paid tokens were counted during the run, so label them with the run's method.
  const method = tokenMethodLabel(
    result?.settings?.cost?.paid_token_method ?? settings.cost.paid_token_method,
  );
  const comparisonSentence = (() => {
    if (extractionCost == null) {
      return "Run a document to estimate extraction and embedding costs.";
    }
    if (pages <= 0 && (paidTokens == null || paidTokens <= 0)) {
      return "No measured page or token counts yet. Select a DocLang file and run Estimate cost so the comparison can use document size.";
    }
    if (paidTokens == null) {
      return "Embedding tokens are missing from this run, so embedding cost cannot be estimated. Re-run Estimate cost on a selected document.";
    }
    if (embeddingRate == null || Number.isNaN(embeddingRate)) {
      return "Enter an embedding price per million tokens to compare extraction with embedding.";
    }
    if (embeddingCost == null) {
      return "Unable to compute embedding cost with the current inputs.";
    }
    if (embeddingCost === 0) {
      return "Embedding cost is zero with the current assumptions, so a ratio is not shown.";
    }
    if (ratio == null || extractionPctOfTotal == null) {
      return "Unable to compute the extraction-versus-embedding comparison.";
    }
    return `Extraction costs approximately ${formatRatio(ratio)} as much as embedding and accounts for ${formatPct(extractionPctOfTotal)} of the combined estimated cost.`;
  })();

  const businessTakeaway = (() => {
    if (
      extractionCost == null ||
      embeddingCost == null ||
      ratio == null ||
      embeddingCost <= 0
    ) {
      return null;
    }
    return {
      ratioLabel: formatRatio(ratio),
      extractionShare: formatPct(extractionPctOfTotal),
      extractionUsd: formatUsd(extractionCost, 2),
      embeddingUsd: formatUsd(embeddingCost, 4),
      savingsUsd: formatUsd(extractionCost, 2),
    };
  })();

  // Stacked bar: ensure embedding remains visible when tiny (labels show exact %)
  const visualExtraction =
    extractionPctOfTotal != null ? Math.max(0, extractionPctOfTotal) : 0;
  const visualEmbeddingRaw =
    embeddingPctOfTotal != null && embeddingPctOfTotal > 0
      ? Math.max(embeddingPctOfTotal, 1.5)
      : 0;
  const visualTotal = visualExtraction + visualEmbeddingRaw;
  const visualEmbedding =
    visualTotal > 100 && visualEmbeddingRaw > 0
      ? Math.max(1.5, 100 - visualExtraction)
      : visualEmbeddingRaw;
  const visualExtractionFinal =
    visualEmbedding > 0 ? 100 - visualEmbedding : visualExtraction;

  const updateCost = (patch: Partial<ProcessSettings["cost"]>) => {
    onSettingsChange({
      ...settings,
      cost: { ...settings.cost, ...patch },
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-2xl tracking-tight">
          PDF extraction vs. embedding cost
        </CardTitle>
        <CardDescription className="max-w-3xl text-sm leading-relaxed text-slate-600">
          Business question: for this document, how much of the pipeline cost is
          Docling SaaS PDF → DocLang extraction, and how much is embedding the
          text afterward? If extraction dominates, keeping DocLang as a durable
          asset means later reingestions can skip the expensive step.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-8">
        {/* Pricing assumptions */}
        <section className="rounded-xl border border-slate-200 bg-white/80 p-4 sm:p-5">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-900">
              Pricing assumptions
            </h3>
            <Badge className="border-slate-200 bg-slate-50 text-slate-700">
              User-entered
            </Badge>
          </div>
          <p className="mb-4 text-xs text-slate-500">
            Rates below are assumptions used for estimates. They are labeled
            verified only when a source and verification date are available
            (none configured here).
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <Label>Extraction provider / plan</Label>
              <Input
                value={settings.cost.extraction_provider ?? "Docling SaaS"}
                onChange={(e) =>
                  updateCost({ extraction_provider: e.target.value })
                }
              />
            </div>
            <div>
              <Label>Extraction price (USD / 1,000 pages)</Label>
              <Input
                type="number"
                step="0.01"
                value={settings.cost.extraction_price_per_1000_pages}
                onChange={(e) =>
                  updateCost({
                    extraction_price_per_1000_pages: Number(e.target.value),
                  })
                }
              />
            </div>
            <div>
              <Label>Embedding provider / model</Label>
              <Input
                placeholder="e.g. text-embedding-3-small"
                value={settings.cost.paid_embedding_model}
                onChange={(e) =>
                  updateCost({ paid_embedding_model: e.target.value })
                }
              />
            </div>
            <div>
              <Label>Embedding price (USD / 1M tokens)</Label>
              <Input
                type="number"
                step="0.0001"
                placeholder="Leave blank if unknown"
                value={
                  settings.cost.embedding_price_per_million_tokens ?? ""
                }
                onChange={(e) =>
                  updateCost({
                    embedding_price_per_million_tokens: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
              />
            </div>
          </div>
        </section>

        {!result ? (
          <p className="text-sm text-slate-500">
            Empty until a process job finishes. Select a document and run a
            cost estimate to populate this comparison.
          </p>
        ) : (
          <>
            {/* Data-source callout */}
            <section className="rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3 sm:px-5">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                What each side of the comparison uses
              </p>
              <div className="mt-2 grid gap-3 text-sm text-slate-700 sm:grid-cols-2">
                <p>
                  <span className="font-semibold text-teal-800">Pages → extraction $:</span>{" "}
                  counted from the{" "}
                  <span className="font-medium">
                    {pageFromPdf ? "original PDF" : "page source"}
                  </span>{" "}
                  ({pageSourceLabel}). Used only for the Docling SaaS page-based
                  price — PDF is not re-extracted here.
                </p>
                <p>
                  <span className="font-semibold text-sky-800">Tokens → embedding $:</span>{" "}
                  prepared from the existing{" "}
                  <span className="font-medium">DocLang</span> artifact
                  (load → chunk → tokenize). That is the text used for the
                  embedding cost estimate.
                </p>
              </div>
            </section>

            {/* Primary cost cards */}
            <section className="grid gap-4 lg:grid-cols-3">
              <CostHeroCard
                title="Extraction: PDF → DocLang"
                amount={formatUsd(extractionCost, 2)}
                basis={`${extractionProvider} · ${formatInt(pages)} pages × $${extractionRate.toFixed(2)} / 1,000 pages`}
                footnote={`Page count source: ${pageSourceLabel}.`}
                accent="teal"
              />
              <CostHeroCard
                title="Embedding: DocLang text → vectors"
                amount={
                  embeddingCost == null
                    ? "Unavailable"
                    : formatUsd(embeddingCost, 4)
                }
                basis={
                  embeddingCost == null
                    ? paidTokens == null
                      ? "Needs a completed run with token counts"
                      : embeddingRate == null || Number.isNaN(Number(embeddingRate))
                        ? "Set embedding price / 1M tokens above"
                        : "Unable to compute with current inputs"
                    : `${embeddingModel} · ${formatInt(paidTokens)} tokens × $${Number(embeddingRate).toFixed(4)} / 1M tokens`
                }
                footnote="Tokens come from chunking the prepared DocLang file — not from parsing the PDF again."
                accent="sky"
              />
              <CostHeroCard
                title="Total: extraction + embedding"
                amount={
                  totalCost == null ? "Unavailable" : formatUsd(totalCost, 4)
                }
                basis={
                  totalCost == null
                    ? "Requires both extraction and embedding estimates"
                    : "Modeled cost to process this document from its original PDF"
                }
                footnote="Combines PDF-based page pricing with DocLang-based token pricing."
                accent="slate"
              />
            </section>

            {/* Comparison sentence + business context */}
            <section className="space-y-4">
              <div className="rounded-xl border border-teal-200 bg-teal-50/60 px-5 py-4">
                <p className="text-base font-medium leading-relaxed text-teal-950">
                  {comparisonSentence}
                </p>
              </div>

              {businessTakeaway ? (
                <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Why DocLang helps for this document
                  </p>
                  <p className="mt-2 text-base font-medium leading-relaxed text-slate-900">
                    Almost all of the modeled cost ({businessTakeaway.extractionShare})
                    is the one-time PDF → DocLang extraction (
                    {businessTakeaway.extractionUsd}). Embedding the prepared text
                    is only about {businessTakeaway.embeddingUsd} — roughly{" "}
                    {businessTakeaway.ratioLabel} cheaper.
                  </p>

                  <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/80 p-4">
                    <p className="text-sm font-semibold text-slate-900">
                      Why reingest the same DocLang again?
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">
                      The PDF content did not change. Downstream choices often do —
                      and each change needs a new pass over the text, not a new PDF
                      extraction.
                    </p>
                    <ul className="mt-3 space-y-2 text-sm leading-relaxed text-slate-700">
                      <li>
                        <span className="font-medium text-slate-900">Different embedding model</span>
                        {" — "}
                        switch from a cheap OpenAI model to another provider, a
                        newer model, or a local model (for example Qwen) without
                        re-running Docling SaaS.
                      </li>
                      <li>
                        <span className="font-medium text-slate-900">Different chunking</span>
                        {" — "}
                        try a new chunk size, overlap, or table handling to improve
                        retrieval quality.
                      </li>
                      <li>
                        <span className="font-medium text-slate-900">Rebuild or retarget the index</span>
                        {" — "}
                        recreate vectors after a schema change, a bad index, or for
                        a second search / RAG store.
                      </li>
                      <li>
                        <span className="font-medium text-slate-900">Development and iteration</span>
                        {" — "}
                        while you build the app you often re-run the same documents
                        dozens of times (new chunk settings, model, prompt, or index
                        wiring). That is day-to-day engineering, not a formal A/B
                        test — but each run still needs tokens and embeddings, not a
                        fresh PDF extraction.
                      </li>
                      <li>
                        <span className="font-medium text-slate-900">A/B or evaluation runs</span>
                        {" — "}
                        a planned comparison of two (or more) embedding / chunking
                        setups on a fixed document set, to pick a winner for
                        production.
                      </li>
                    </ul>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg border border-rose-100 bg-rose-50/70 p-4">
                      <p className="text-xs font-semibold uppercase tracking-wide text-rose-800">
                        Without reusable DocLang
                      </p>
                      <p className="mt-2 text-sm leading-relaxed text-rose-950">
                        Each of those reingestions starts from the PDF again. You
                        pay extraction ({businessTakeaway.extractionUsd}) plus
                        embedding every time.
                      </p>
                    </div>
                    <div className="rounded-lg border border-emerald-100 bg-emerald-50/70 p-4">
                      <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">
                        With DocLang kept on hand
                      </p>
                      <p className="mt-2 text-sm leading-relaxed text-emerald-950">
                        Extraction is already done. Load the same DocLang, re-chunk,
                        re-embed — about {businessTakeaway.embeddingUsd} instead of{" "}
                        {businessTakeaway.extractionUsd}. Estimated extraction
                        avoided per reingestion:{" "}
                        <span className="font-semibold tabular-nums">
                          {businessTakeaway.savingsUsd}
                        </span>
                        .
                      </p>
                    </div>
                  </div>
                  <p className="mt-4 text-sm leading-relaxed text-slate-600">
                    DocLang is the durable intermediate format: structure and text
                    stay available without calling Docling SaaS again. That is why
                    preparing once and reusing matters when extraction is{" "}
                    {businessTakeaway.ratioLabel} the embedding cost.
                  </p>
                </div>
              ) : null}
            </section>

            {/* Transparent calculations */}
            <section className="rounded-xl border border-slate-200 p-4 sm:p-5">
              <h3 className="text-sm font-semibold text-slate-900">
                How these estimates are calculated
              </h3>
              <ul className="mt-3 space-y-2 text-sm text-slate-700">
                <li>
                  <span className="font-medium">Extraction cost</span> ={" "}
                  {formatInt(pages)} pages (from {pageSourceLabel}) ÷ 1,000 × $
                  {extractionRate.toFixed(2)} ={" "}
                  <span className="tabular-nums font-semibold">
                    {formatUsd(extractionCost, 4)}
                  </span>
                </li>
                <li>
                  <span className="font-medium">Embedding cost</span> ={" "}
                  {paidTokens == null ? "—" : formatInt(paidTokens)} tokens
                  (from DocLang chunks) ÷ 1,000,000 ×{" "}
                  {embeddingRate == null
                    ? "(rate unavailable)"
                    : `$${Number(embeddingRate).toFixed(4)}`}{" "}
                  ={" "}
                  <span className="tabular-nums font-semibold">
                    {embeddingCost == null
                      ? "Unavailable"
                      : formatUsd(embeddingCost, 4)}
                  </span>
                </li>
                <li>
                  <span className="font-medium">Total cost</span> = extraction +
                  embedding ={" "}
                  <span className="tabular-nums font-semibold">
                    {totalCost == null
                      ? "Unavailable"
                      : formatUsd(totalCost, 4)}
                  </span>
                </li>
              </ul>
              <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
                <p className="font-medium text-slate-800">
                  Embedding token count used for the estimate
                </p>
                <p className="mt-1">
                  <strong>Paid-model tokens (drives the estimate):</strong>{" "}
                  {paidTokens == null ? "—" : formatInt(paidTokens)} via{" "}
                  {method.label}
                  {method.exact
                    ? " — exact for this encoding (not a live provider bill)."
                    : " — approximate proxy; not a verified provider tokenizer."}
                </p>
                <p className="mt-1">
                  <strong>Measured local tokens (reference):</strong>{" "}
                  {formatInt(localTokens)} with the Qwen embedding tokenizer
                  (exact for local encoding; kept separate from the paid
                  estimate).
                </p>
              </div>
            </section>

            {/* Stacked breakdown */}
            <section className="rounded-xl border border-slate-200 p-4 sm:p-5">
              <h3 className="text-sm font-semibold text-slate-900">
                Cost breakdown
              </h3>
              {totalCost != null &&
              extractionCost != null &&
              embeddingCost != null ? (
                <div className="mt-4 space-y-3">
                  <div className="flex h-4 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full bg-teal-600"
                      style={{ width: `${visualExtractionFinal}%` }}
                      title={`Extraction ${formatUsd(extractionCost, 4)}`}
                    />
                    <div
                      className="h-full bg-sky-500"
                      style={{ width: `${visualEmbedding}%` }}
                      title={`Embedding ${formatUsd(embeddingCost, 4)}`}
                    />
                  </div>
                  <div className="flex flex-col gap-2 text-sm sm:flex-row sm:justify-between">
                    <p className="text-slate-700">
                      <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm bg-teal-600" />
                      Extraction{" "}
                      <span className="font-semibold tabular-nums">
                        {formatUsd(extractionCost, 4)}
                      </span>{" "}
                      ({formatPct(extractionPctOfTotal)})
                    </p>
                    <p className="text-slate-700">
                      <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm bg-sky-500" />
                      Embedding{" "}
                      <span className="font-semibold tabular-nums">
                        {formatUsd(embeddingCost, 4)}
                      </span>{" "}
                      ({formatPct(embeddingPctOfTotal, 2)})
                    </p>
                  </div>
                  {embeddingPctOfTotal != null && embeddingPctOfTotal < 2 ? (
                    <p className="text-[11px] text-slate-500">
                      Embedding share is visually enlarged slightly in the bar
                      so the small segment stays readable; percentages above are
                      exact.
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="mt-3 text-sm text-slate-500">
                  Breakdown available once both extraction and embedding
                  estimates can be computed.
                </p>
              )}
            </section>

            {/* Secondary measurements */}
            <section className="rounded-xl border border-slate-200 bg-slate-50/50 p-4 sm:p-5">
              <h3 className="text-sm font-semibold text-slate-900">
                Supporting measurements from this run
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                Measured locally. Separate from SaaS extraction timing or paid
                embedding API calls.
              </p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-lg border border-slate-200 bg-white p-3">
                  <p className="text-[11px] uppercase tracking-wide text-slate-500">
                    Pages
                  </p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {formatInt(pages)}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-white p-3">
                  <p className="text-[11px] uppercase tracking-wide text-slate-500">
                    Chunks
                  </p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {formatInt(chunkCount)}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-white p-3">
                  <p className="text-[11px] uppercase tracking-wide text-slate-500">
                    Local tokens (Qwen)
                  </p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {formatInt(localTokens)}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-white p-3">
                  <p className="text-[11px] uppercase tracking-wide text-slate-500">
                    Local preparation time
                  </p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {formatSeconds(prepSeconds)}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-500">
                    Load + chunk + tokenize
                    {embedSkipped ? ". Embedding was skipped." : "."}
                  </p>
                </div>
              </div>
            </section>

            {/* Assumptions note */}
            <p className="text-xs leading-relaxed text-slate-500">
              Provider charges are estimated from document size and the selected
              rates. This run measured local preparation only. Storage, network,
              and local compute costs are excluded. The business takeaway above
              assumes DocLang can be stored and reused; it does not include
              storage fees.
            </p>

            <details className="text-xs text-slate-600">
              <summary className="cursor-pointer font-medium text-slate-700">
                Technical details and exclusions
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                <li>
                  Fresh PDF extraction is not executed by this application;
                  extraction $ uses page count and your SaaS rate assumption.
                </li>
                <li>
                  Paid embedding API calls are not executed; embedding $ uses
                  counted input tokens and your selected rate.
                </li>
                <li>
                  Local Qwen token counts and paid-model token estimates are
                  kept distinct.
                </li>
                {(result.cost?.notes || []).map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </details>

            <div className="flex flex-wrap gap-2">
              <a
                className="inline-flex h-9 items-center rounded-md border border-slate-300 px-3 text-sm hover:bg-slate-50"
                href={client.exportUrl(result.job_id, "json")}
              >
                Export JSON
              </a>
              <a
                className="inline-flex h-9 items-center rounded-md border border-slate-300 px-3 text-sm hover:bg-slate-50"
                href={client.exportUrl(result.job_id, "csv")}
              >
                Export CSV
              </a>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
