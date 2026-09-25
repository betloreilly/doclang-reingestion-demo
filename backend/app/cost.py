"""Cost calculator for avoided PDF extraction vs prepared DocLang."""

from __future__ import annotations

from typing import List, Optional

from .schemas import CostBreakdown, CostSettings, TimeSavings


def compute_costs(
    pages: int,
    paid_tokens: Optional[int],
    settings: CostSettings,
) -> CostBreakdown:
    p = max(0, pages)
    r = float(settings.extraction_price_per_1000_pages)
    n = max(0, int(settings.future_reingestions))
    avoided = (p / 1000.0) * r
    future_savings = n * avoided

    e = settings.embedding_price_per_million_tokens
    embedding_est: Optional[float] = None
    fresh: Optional[float] = None
    prepared: Optional[float] = None
    extraction_to_embedding_ratio: Optional[float] = None
    embedding_pct_of_extraction: Optional[float] = None
    extraction_pct_of_fresh_total: Optional[float] = None
    embedding_pct_of_fresh_total: Optional[float] = None
    pricing_available = e is not None and paid_tokens is not None
    notes: List[str] = [
        "Prepared DocLang artifacts already exist; historical creation cost is outside this future-reingestion comparison.",
        "Storage, network, and local compute costs are excluded.",
        "Local embedding execution produces no paid embedding API charge.",
        "Estimated provider charges differ from actual local operating costs.",
        "Comparison assumes the same embedding input volume for fresh-PDF and prepared-DocLang strategies.",
        "Fresh PDF extraction is not executed or token-counted by this application.",
        "Extraction price is a user-provided SaaS pricing assumption.",
    ]
    if pricing_available:
        embedding_est = (float(paid_tokens) / 1_000_000.0) * float(e)
        prepared = embedding_est
        fresh = avoided + embedding_est
        if embedding_est > 0:
            extraction_to_embedding_ratio = avoided / embedding_est
            embedding_pct_of_extraction = (embedding_est / avoided * 100.0) if avoided > 0 else None
        if fresh and fresh > 0:
            extraction_pct_of_fresh_total = avoided / fresh * 100.0
            embedding_pct_of_fresh_total = embedding_est / fresh * 100.0
        notes.append(
            "Ratio cards compare modeled SaaS extraction charge to modeled paid embedding charge per reingestion."
        )
    else:
        notes.append(
            "Embedding pricing unavailable — totals and ratios that require E or T are marked unavailable."
        )

    return CostBreakdown(
        pages=p,
        extraction_price_per_1000_pages=r,
        avoided_extraction_per_reingestion=avoided,
        paid_embedding_tokens=paid_tokens,
        embedding_price_per_million_tokens=e,
        embedding_estimate_per_reingestion=embedding_est,
        hypothetical_fresh_pdf_cost=fresh,
        prepared_doclang_cost=prepared,
        extraction_to_embedding_ratio=extraction_to_embedding_ratio,
        embedding_pct_of_extraction=embedding_pct_of_extraction,
        extraction_pct_of_fresh_total=extraction_pct_of_fresh_total,
        embedding_pct_of_fresh_total=embedding_pct_of_fresh_total,
        future_extraction_savings=future_savings,
        future_reingestions=n,
        embedding_pricing_available=pricing_available,
        notes=notes,
    )


def compute_time_savings(
    doclang_load_seconds: float,
    downstream_seconds: float,
    settings: CostSettings,
) -> TimeSavings:
    baseline = settings.extraction_baseline_seconds
    if baseline is None:
        return TimeSavings(
            available=False,
            baseline_seconds=None,
            doclang_load_seconds=doclang_load_seconds,
            note=(
                "Time-saved metrics unavailable without a user-supplied extraction-time baseline."
            ),
        )
    savings = float(baseline) - float(doclang_load_seconds)
    modeled_fresh = float(baseline) + float(downstream_seconds)
    modeled_prepared = float(doclang_load_seconds) + float(downstream_seconds)
    note = (
        "Estimated extraction-stage time savings compare the user-supplied baseline "
        "with measured DocLang loading duration. Comparability depends on matching "
        "documents and execution conditions. The modeled full-pipeline estimate replaces "
        "DocLang loading with the supplied extraction duration while holding downstream "
        "timings constant — a projection, not a measured PDF run."
    )
    if settings.extraction_baseline_note:
        note = f"{note} Source note: {settings.extraction_baseline_note}"
    return TimeSavings(
        available=True,
        baseline_seconds=float(baseline),
        doclang_load_seconds=doclang_load_seconds,
        estimated_extraction_stage_savings_seconds=savings,
        modeled_full_pipeline_fresh_seconds=modeled_fresh,
        modeled_full_pipeline_prepared_seconds=modeled_prepared,
        note=note,
    )
