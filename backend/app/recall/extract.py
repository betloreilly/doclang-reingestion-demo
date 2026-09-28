"""Per-page markdown from DocLang and Unstructured, chunked with the same chunker."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from ..chunking import chunk_markdown

PageMarkdown = Dict[int, str]

_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+\S")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")


@dataclass
class RecallChunk:
    doc_name: str
    page: int
    text: str


def doclang_pages(document: Any) -> PageMarkdown:
    """Group DocLang body items by provenance page, keeping headings and tables."""
    from docling_core.types.doc import TableItem

    pages: Dict[int, List[str]] = defaultdict(list)
    for item, _level in document.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        label = str(getattr(item, "label", ""))
        if isinstance(item, TableItem):
            text = item.export_to_markdown(doc=document)
        elif label in {"title", "section_header"}:
            text = f"## {item.text}"
        elif label == "list_item":
            text = f"- {item.text}"
        else:
            text = getattr(item, "text", "") or ""
        if text.strip():
            pages[prov[0].page_no].append(text.strip())
    return {page: "\n\n".join(parts) for page, parts in pages.items()}


def unstructured_pages(payload: Dict[str, Any]) -> PageMarkdown:
    """Group Unstructured elements by page_number; Title elements become headings."""
    pages: Dict[int, List[str]] = defaultdict(list)
    for el in payload.get("elements", []):
        page = el.get("page_number")
        text = (el.get("text") or "").strip()
        if page is None or not text:
            continue
        kind = el.get("type")
        if kind == "Title":
            text = f"## {text}"
        elif kind == "ListItem":
            text = f"- {text}"
        pages[int(page)].append(text)
    return {page: "\n\n".join(parts) for page, parts in pages.items()}


def align_to_pdf_pages(
    pages: PageMarkdown, pdf_page_texts: List[str], jump_penalty: float = 0.3
) -> Tuple[PageMarkdown, int]:
    """
    Renumber pages to the PDF's page numbers when the archive dropped pages.

    Some archives omit a PDF page and renumber the rest, which shifts every later
    page. The page offset can only grow; the best non-decreasing offset sequence is
    chosen globally (dynamic programming) so one noisy page cannot trigger a jump.
    Returns (remapped pages, number of pages whose number changed).
    """
    if not pages:
        return pages, 0
    pdf_tokens = [set(normalized_tokens(t)) for t in pdf_page_texts]
    ordered = sorted(pages)
    offsets = range(max(0, len(pdf_tokens) - ordered[-1]) + 1)

    def score(tokens: set, pdf_page: int) -> float:
        if not tokens or not 1 <= pdf_page <= len(pdf_tokens):
            return 0.0
        return len(tokens & pdf_tokens[pdf_page - 1]) / len(tokens)

    best: List[List[float]] = []
    back: List[List[int]] = []
    for i, page in enumerate(ordered):
        tokens = set(normalized_tokens(pages[page]))
        row, row_back = [], []
        for o in offsets:
            prev_o = o
            if i:
                prev_o = max(range(o + 1), key=lambda p: best[i - 1][p] - (jump_penalty if p != o else 0.0))
            carried = best[i - 1][prev_o] - (jump_penalty if prev_o != o else 0.0) if i else 0.0
            row.append(carried + score(tokens, page + o))
            row_back.append(prev_o)
        best.append(row)
        back.append(row_back)

    o = max(offsets, key=lambda k: best[-1][k])
    chosen = [0] * len(ordered)
    for i in range(len(ordered) - 1, -1, -1):
        chosen[i] = o
        o = back[i][o]
    remapped = {page + off: pages[page] for page, off in zip(ordered, chosen)}
    return remapped, sum(1 for off in chosen if off)


def page_chunks(
    pages: PageMarkdown, doc_name: str, chunk_size: int, chunk_overlap: int
) -> List[RecallChunk]:
    """Chunk each page separately so every chunk maps to exactly one page."""
    chunks: List[RecallChunk] = []
    heading = ""
    for page in sorted(pages):
        markdown = pages[page]
        if heading and not _HEADING_LINE_RE.match(markdown.lstrip()):
            markdown = f"{heading}\n\n{markdown}"
        page_chunks_, _ = chunk_markdown(
            markdown, f"{doc_name}::p{page}", chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        chunks.extend(RecallChunk(doc_name, page, c.text) for c in page_chunks_ if c.text.strip())
        for line in markdown.splitlines():
            if _HEADING_LINE_RE.match(line.strip()):
                heading = line.strip()
    return chunks


def normalized_tokens(text: str) -> List[str]:
    return [t.replace(",", "") for t in _TOKEN_RE.findall(text.lower())]


def evidence_coverage(evidence_text: str, page_text: str) -> float:
    """Share of unique evidence tokens present in the extracted page text."""
    wanted = set(normalized_tokens(evidence_text))
    if not wanted:
        return 1.0
    have = set(normalized_tokens(page_text))
    return len(wanted & have) / len(wanted)
