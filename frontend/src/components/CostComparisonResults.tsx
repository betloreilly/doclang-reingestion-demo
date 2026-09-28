"use client";

import { formatInt, formatPct, formatRatio, formatSeconds, formatUsd } from "@/lib/utils";
import type {
  CompareExtractionVendor,
  ProcessSettings,
  RunResult,
} from "@/lib/api";
import { Badge, Input, Label } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { client } from "@/lib/api";

const COMPARE_BAR_COLORS = ["bg-amber-500", "bg-violet-500", "bg-rose-500", "bg-orange-500"];

const DEFAULT_COMPARE_VENDORS: CompareExtractionVendor[] = [
  {
    provider: "Unstructured.io",
    price_per_1000_pages: 15.0,
    note: "$0.015 / page (pay-as-you-go list rate)",
  },
  {
    provider: "Azure Document Intelligence (Layout)",
    price_per_1000_pages: 10.0,
    note: "S0 prebuilt / Layout · $10 / 1,000 pages (Read is $1.50 / 1,000)",
  },
  {
    provider: "Snowflake AI_PARSE_DOCUMENT (Layout, global)",
    price_per_1000_pages: 7.32,
    note: "Layout mode, global routing ($8.052 with regional routing)",
  },
];

function resolveCompareVendors(
  cost: ProcessSettings["cost"]
): CompareExtractionVendor[] {
  if (cost.compare_extraction_vendors && cost.compare_extraction_vendors.length > 0) {
    return cost.compare_extraction_vendors;
  }
  if (
    cost.compare_extraction_provider &&
    cost.compare_extraction_price_per_page != null
  ) {
    return [
      {
        provider: cost.compare_extraction_provider,
        price_per_1000_pages: cost.compare_extraction_price_per_page * 1000,
        note: "Migrated from USD / page",
      },
    ];
  }
  return DEFAULT_COMPARE_VENDORS;
}

function tokenMethodLabel(method: ProcessSettings["cost"]["paid_token_method"]): {
  label: string;
  exact: boolean;
} {
  switch (method) {
    case "tiktoken_cl100k":
      return {
        label: "tiktoken cl100k_base",
        exact: true,
      };
    case "tiktoken_o200k":
      return {
        label: "tiktoken o200k_base",
        exact: true,
      };
    case "same_as_local":
      return {
        label: "same as local Qwen tokenizer",
        exact: false,
      };
    case "chars_div_4":
      return { label: "characters ÷ 4", exact: false };
    default:
      return { label: String(method), exact: false };
  }
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
  const paidTokens = result?.paid_tokens_total ?? null;
  const prepSeconds =
    result?.stage_timings?.find((t) => t.name === "process_excluding_download")
      ?.seconds ?? null;
  const embedSkipped = Boolean(result?.embedding_meta?.skipped);
  const chunkCount = result?.chunks_total ?? 0;
  const tokensPerPage =
    pages > 0 && paidTokens != null && paidTokens > 0
      ? paidTokens / pages
      : null;

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
      if (s === "pdf") return "PDF page count";
      if (s === "doclang_pages") return "DocLang pages";
      if (s === "manual") return "manual";
      return s;
    }
    return pageSources.join(", ");
  })();

  const extractionRate = settings.cost.extraction_price_per_1000_pages;
  const embeddingRate = settings.cost.embedding_price_per_million_tokens;
  const extractionProvider =
    settings.cost.extraction_provider || "Docling SaaS";
  const compareVendors = resolveCompareVendors(settings.cost);

  const extractionCost =
    result != null ? (pages / 1000) * extractionRate : null;
  const embeddingCost =
    result != null &&
    paidTokens != null &&
    embeddingRate != null &&
    !Number.isNaN(embeddingRate)
      ? (paidTokens / 1_000_000) * embeddingRate
      : null;
  const compareRows = compareVendors.map((vendor, index) => {
    const cost =
      result != null && !Number.isNaN(vendor.price_per_1000_pages)
        ? (pages / 1000) * vendor.price_per_1000_pages
        : null;
    const total =
      cost != null && embeddingCost != null ? cost + embeddingCost : null;
    return {
      ...vendor,
      index,
      extractionCost: cost,
      totalCost: total,
      barColor: COMPARE_BAR_COLORS[index % COMPARE_BAR_COLORS.length],
    };
  });
  const totalCost =
    extractionCost != null && embeddingCost != null
      ? extractionCost + embeddingCost
      : null;
  const maxVendorTotal = Math.max(
    totalCost ?? 0,
    ...compareRows.map((r) => r.totalCost ?? 0)
  );

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

  const method = tokenMethodLabel(
    result?.settings?.cost?.paid_token_method ?? settings.cost.paid_token_method,
  );

  const businessTakeaway = (() => {
    if (
      extractionCost == null ||
      embeddingCost == null ||
      ratio == null ||
      embeddingCost <= 0
    ) {
      return null;
    }
    const ts = result?.time_savings;
    const loadSeconds = ts?.doclang_load_seconds ?? null;
    const baselineSeconds = ts?.available ? ts.baseline_seconds ?? null : null;
    const stageSavings =
      ts?.available && ts.estimated_extraction_stage_savings_seconds != null
        ? ts.estimated_extraction_stage_savings_seconds
        : null;
    return {
      ratioLabel: formatRatio(ratio),
      extractionShare: formatPct(extractionPctOfTotal),
      extractionUsd: formatUsd(extractionCost, 2),
      embeddingUsd: formatUsd(embeddingCost, 4),
      prepLabel: formatSeconds(prepSeconds),
      loadLabel: loadSeconds != null ? formatSeconds(loadSeconds) : null,
      baselineLabel:
        baselineSeconds != null ? formatSeconds(baselineSeconds) : null,
      stageSavingsLabel:
        stageSavings != null ? formatSeconds(stageSavings) : null,
    };
  })();

  const updateCost = (patch: Partial<ProcessSettings["cost"]>) => {
    onSettingsChange({
      ...settings,
      cost: { ...settings.cost, ...patch },
    });
  };

  const updateCompareVendor = (
    index: number,
    patch: Partial<CompareExtractionVendor>
  ) => {
    const next = compareVendors.map((v, i) =>
      i === index ? { ...v, ...patch } : v
    );
    updateCost({ compare_extraction_vendors: next });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-2xl tracking-tight">
          PDF extraction vs. embedding cost
        </CardTitle>
        <CardDescription className="max-w-3xl text-sm leading-relaxed text-slate-600">
          For static PDF archives, layout extraction is usually ~99% of the bill and
          embedding is ~1%. If you change chunking or the embedding model later, a
          normal pipeline re-parses the PDF and pays again. DocLang lets you parse
          once, then re-chunk / re-embed cheaply.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <h3 className="text-sm font-semibold text-slate-900">
            Pricing assumptions
          </h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <Label>Primary extraction</Label>
              <Input
                value={settings.cost.extraction_provider ?? "Docling SaaS"}
                onChange={(e) =>
                  updateCost({ extraction_provider: e.target.value })
                }
              />
            </div>
            <div>
              <Label>Primary (USD / 1,000 pages)</Label>
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
              <Label>Embedding model</Label>
              <Input
                placeholder="e.g. text-embedding-3-small"
                value={settings.cost.paid_embedding_model}
                onChange={(e) =>
                  updateCost({ paid_embedding_model: e.target.value })
                }
              />
            </div>
            <div>
              <Label>Embedding (USD / 1M tokens)</Label>
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

          <h4 className="mt-5 text-sm font-semibold text-slate-900">
            Compare vendors (USD / 1,000 pages)
          </h4>
          <div className="mt-2 space-y-2">
            {compareVendors.map((vendor, index) => (
              <div
                key={index}
                className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_7.5rem]"
              >
                <Input
                  value={vendor.provider}
                  onChange={(e) =>
                    updateCompareVendor(index, { provider: e.target.value })
                  }
                />
                <Input
                  type="number"
                  step="0.01"
                  value={vendor.price_per_1000_pages}
                  onChange={(e) =>
                    updateCompareVendor(index, {
                      price_per_1000_pages: Number(e.target.value),
                    })
                  }
                />
              </div>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            Defaults: Unstructured $15 ·{" "}
            <a
              href="https://azure.microsoft.com/en-us/pricing/details/document-intelligence/"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              Azure Layout
            </a>{" "}
            $10 · Snowflake Layout (global) $7.32. Rates are editable assumptions.
          </p>
        </section>

        {!result ? (
          <p className="text-sm text-slate-500">
            Run a cost estimate to populate this comparison.
          </p>
        ) : (
          <>
            <p className="text-xs text-slate-500">
              {formatInt(pages)} pages ({pageSourceLabel}) ·{" "}
              {formatInt(paidTokens)} embedding tokens ({method.label})
              {tokensPerPage != null
                ? ` · ${formatInt(Math.round(tokensPerPage))} tokens / page`
                : ""}{" "}
              · {formatInt(chunkCount)} chunks · {formatSeconds(prepSeconds)}{" "}
              local prep
              {embedSkipped ? " · embedding skipped" : ""}
            </p>
            {tokensPerPage != null ? (
              <p className="text-[11px] leading-relaxed text-slate-400">
                Tokens / page uses chunked embedding input (overlap and repeated
                headings / table headers counted). Typical dense 10-K runs land
                around 800–1,400 with the default chunk settings.
              </p>
            ) : null}

            {/* Cost breakdown graph */}
            <section className="rounded-xl border border-slate-200 p-4 sm:p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold text-slate-900">
                  Cost breakdown by extraction vendor
                </h3>
                <Badge className="border-amber-200 bg-amber-50 text-amber-900">
                  Estimated
                </Badge>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Same page count and DocLang embedding tokens; only the extraction
                rate changes. Bars share one scale.
              </p>

              {totalCost != null &&
              extractionCost != null &&
              embeddingCost != null ? (
                <div className="mt-5 space-y-6">
                  <VendorBreakdownRow
                    label={extractionProvider}
                    total={totalCost}
                    extractionCost={extractionCost}
                    embeddingCost={embeddingCost}
                    extractionColor="bg-teal-600"
                    maxTotal={maxVendorTotal}
                    enlargeEmbedding={
                      embeddingPctOfTotal != null && embeddingPctOfTotal < 2
                    }
                  />
                  {compareRows.map((row) =>
                    row.extractionCost != null && row.totalCost != null ? (
                      <VendorBreakdownRow
                        key={row.index}
                        label={row.provider}
                        total={row.totalCost}
                        extractionCost={row.extractionCost}
                        embeddingCost={embeddingCost}
                        extractionColor={row.barColor}
                        maxTotal={maxVendorTotal}
                        enlargeEmbedding
                      />
                    ) : null
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm text-slate-500">
                  Enter an embedding price to show the breakdown.
                </p>
              )}
            </section>

            {businessTakeaway ? (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Why DocLang helps
                </p>
                <p className="mt-2 text-sm leading-relaxed text-slate-800">
                  About {businessTakeaway.extractionShare} of this estimate (
                  {businessTakeaway.extractionUsd}) is PDF → DocLang extraction.
                  Embedding is only {businessTakeaway.embeddingUsd} — roughly{" "}
                  {businessTakeaway.ratioLabel} smaller. If the PDF never changes,
                  keep DocLang and pay the small amount again when you reingest —
                  not the full extraction bill.
                  {businessTakeaway.prepLabel !== "—" ? (
                    <>
                      {" "}
                      You also save time: this run’s local DocLang prep finished in{" "}
                      {businessTakeaway.prepLabel}
                      {businessTakeaway.stageSavingsLabel &&
                      businessTakeaway.baselineLabel ? (
                        <>
                          {" "}
                          (about {businessTakeaway.stageSavingsLabel} faster than
                          your {businessTakeaway.baselineLabel} extraction
                          baseline)
                        </>
                      ) : (
                        <>
                          {" "}
                          — later reingestions skip the cloud parse wait and stay
                          on local load → chunk → tokenize
                        </>
                      )}
                      .
                    </>
                  ) : null}
                </p>

                <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/80 p-4">
                  <p className="text-sm font-semibold text-slate-900">
                    When do you reingest the same DocLang again?
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-slate-600">
                    The PDF did not change. Your pipeline choices often do — and each
                    change needs a new pass over the text, not a new PDF extraction.
                  </p>
                  <ul className="mt-3 space-y-2 text-sm leading-relaxed text-slate-700">
                    <li>
                      <span className="font-medium text-slate-900">
                        Different embedding model
                      </span>
                      {" — "}
                      switch provider, upgrade the model, or try a local model (for
                      example Qwen) without calling Docling SaaS again.
                    </li>
                    <li>
                      <span className="font-medium text-slate-900">
                        Different chunking
                      </span>
                      {" — "}
                      change chunk size, overlap, or table packing to improve
                      retrieval.
                    </li>
                    <li>
                      <span className="font-medium text-slate-900">
                        Rebuild or retarget the index
                      </span>
                      {" — "}
                      recreate vectors after a schema change, a bad index, or for a
                      second search / RAG store (including OpenSearch).
                    </li>
                    <li>
                      <span className="font-medium text-slate-900">
                        Development and iteration
                      </span>
                      {" — "}
                      while you build, you often re-run the same documents many times
                      (new settings, model, prompts, wiring). Each run still needs
                      tokens and embeddings — not a fresh PDF parse.
                    </li>
                    <li>
                      <span className="font-medium text-slate-900">
                        A/B or evaluation runs
                      </span>
                      {" — "}
                      compare two or more embedding / chunking setups on a fixed
                      document set and pick a winner for production.
                    </li>
                  </ul>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border border-rose-100 bg-rose-50/60 p-3">
                    <p className="text-xs font-semibold text-rose-800">
                      Without reusable DocLang
                    </p>
                    <p className="mt-1 text-sm text-rose-950">
                      Each of those reingestions starts from the PDF again. You pay
                      extraction ({businessTakeaway.extractionUsd}) plus embedding
                      every time.
                    </p>
                  </div>
                  <div className="rounded-lg border border-emerald-100 bg-emerald-50/60 p-3">
                    <p className="text-xs font-semibold text-emerald-800">
                      With DocLang kept on hand
                    </p>
                    <p className="mt-1 text-sm text-emerald-950">
                      Load the same file, re-chunk, re-embed — about{" "}
                      {businessTakeaway.embeddingUsd} instead of{" "}
                      {businessTakeaway.extractionUsd}.
                    </p>
                  </div>
                </div>
              </section>
            ) : null}

            <p className="text-xs text-slate-500">
              Estimates only — no SaaS extraction or paid embedding calls are
              made. Storage and network costs are excluded.
            </p>

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

function VendorBreakdownRow({
  label,
  total,
  extractionCost,
  embeddingCost,
  extractionColor,
  maxTotal,
  enlargeEmbedding,
}: {
  label: string;
  total: number;
  extractionCost: number;
  embeddingCost: number;
  extractionColor: string;
  maxTotal: number;
  enlargeEmbedding?: boolean;
}) {
  const extractionPct = total > 0 ? (extractionCost / total) * 100 : 0;
  const embeddingPct = total > 0 ? (embeddingCost / total) * 100 : 0;
  const extractionWidth = maxTotal > 0 ? (extractionCost / maxTotal) * 100 : 0;
  const embeddingWidth =
    maxTotal > 0
      ? Math.max(
          (embeddingCost / maxTotal) * 100,
          enlargeEmbedding && embeddingPct < 2 ? 1.5 : 0
        )
      : 0;

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium text-slate-800">{label}</p>
        <p className="shrink-0 tabular-nums text-sm font-semibold text-slate-900">
          {formatUsd(total, 4)}
        </p>
      </div>
      <div className="flex h-3.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full ${extractionColor}`}
          style={{ width: `${extractionWidth}%` }}
          title={`${label} ${formatUsd(extractionCost, 4)}`}
        />
        <div
          className="h-full bg-sky-500"
          style={{ width: `${embeddingWidth}%` }}
          title={`Embedding ${formatUsd(embeddingCost, 4)}`}
        />
      </div>
      <div className="mt-2 space-y-0.5 text-xs tabular-nums text-slate-600">
        <p>
          <span className={`mr-1.5 inline-block h-2 w-2 rounded-sm ${extractionColor}`} />
          {label}{" "}
          <span className="font-medium text-slate-800">
            {formatUsd(extractionCost, 4)}
          </span>{" "}
          ({formatPct(extractionPct)})
        </p>
        <p>
          <span className="mr-1.5 inline-block h-2 w-2 rounded-sm bg-sky-500" />
          Embedding{" "}
          <span className="font-medium text-slate-800">
            {formatUsd(embeddingCost, 4)}
          </span>{" "}
          ({formatPct(embeddingPct, 2)})
        </p>
      </div>
    </div>
  );
}
