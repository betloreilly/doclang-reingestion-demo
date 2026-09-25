"""Deterministic chunking that preserves headings and table structure."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    kind: str = "text"


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _split_markdown_blocks(markdown: str) -> List[Tuple[str, str]]:
    """Yield (kind, text) blocks: heading | table | paragraph."""
    lines = markdown.splitlines()
    blocks: List[Tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _HEADING_RE.match(line.strip()):
            blocks.append(("heading", line.strip()))
            i += 1
            continue
        if line.strip().startswith("|"):
            table_lines = [line]
            i += 1
            while i < len(lines) and (
                lines[i].strip().startswith("|") or _TABLE_SEP_RE.match(lines[i].strip())
            ):
                table_lines.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(table_lines)))
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("|") and not _HEADING_RE.match(lines[i].strip()):
            para.append(lines[i])
            i += 1
        blocks.append(("paragraph", "\n".join(para)))
    return blocks


def _compact_table_line(line: str) -> str:
    """Drop the column-alignment padding added by the markdown exporter; cell text is kept."""
    stripped = line.strip()
    if _TABLE_SEP_RE.match(stripped):
        cells = stripped.strip("|").split("|")
        return "|" + "|".join("---" for _ in cells) + "|"
    return re.sub(r" {2,}", " ", stripped)


def _table_header_and_rows(table_text: str) -> Tuple[str, List[str]]:
    lines = [_compact_table_line(ln) for ln in table_text.splitlines() if ln.strip()]
    if not lines:
        return "", []
    header_lines: List[str] = []
    rows: List[str] = []
    seen_sep = False
    for ln in lines:
        if _TABLE_SEP_RE.match(ln.strip()):
            seen_sep = True
            header_lines.append(ln)
            continue
        if not seen_sep and not rows:
            header_lines.append(ln)
        else:
            rows.append(ln)
    if not header_lines and rows:
        return rows[0], rows[1:]
    return "\n".join(header_lines), rows


def _overlap_tail(text: str, overlap: int) -> str:
    """Last `overlap` characters, starting at a word boundary when one is near."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    cut = tail.find(" ")
    if 0 <= cut < len(tail) // 2:
        tail = tail[cut + 1 :]
    return tail.strip()


def _pack_units(
    units: Sequence[Tuple[str, str]],
    document_id: str,
    chunk_size: int,
    overlap: int,
) -> List[Chunk]:
    chunks: List[Chunk] = []
    parts: List[str] = []
    carry = ""
    heading = ""

    def joined(extra: Optional[str] = None) -> str:
        seq = ([carry] if carry else []) + parts + ([extra] if extra else [])
        return "\n\n".join(seq)

    def add_chunk(text: str, kind: str) -> None:
        chunks.append(
            Chunk(
                chunk_id=f"{document_id}::chunk-{len(chunks):04d}",
                document_id=document_id,
                text=text,
                kind=kind,
            )
        )

    def emit() -> None:
        nonlocal parts, carry
        if not parts:
            return
        text = joined().strip()
        add_chunk(text, "mixed")
        carry = _overlap_tail(text, overlap)
        parts = []

    def with_heading(text: str) -> str:
        if not heading:
            return text
        prefixed = f"{heading}\n\n{text}"
        return prefixed if len(prefixed) <= chunk_size else text

    for kind, text in units:
        if kind == "heading":
            heading = text
            piece = text
        else:
            piece = with_heading(text) if not parts else text

        if len(piece) > chunk_size:
            emit()
            carry = ""
            step = max(chunk_size - overlap, 1)
            for start in range(0, len(piece), step):
                add_chunk(piece[start : start + chunk_size], kind)
                if start + chunk_size >= len(piece):
                    break
            continue

        if parts and len(joined(piece)) > chunk_size:
            emit()
            if kind != "heading":
                piece = with_heading(text)
        if carry and len(joined(piece)) > chunk_size:
            carry = ""
        parts.append(piece)

    emit()
    return chunks


def chunk_markdown(
    markdown: str,
    document_id: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> Tuple[List[Chunk], float]:
    started = time.perf_counter()
    chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))
    blocks = _split_markdown_blocks(markdown)
    units: List[Tuple[str, str]] = []
    heading = ""

    for kind, text in blocks:
        if kind == "heading":
            heading = text
        if kind != "table":
            units.append((kind, text))
            continue
        header, rows = _table_header_and_rows(text)
        if not rows:
            units.append(("table", header or text))
            continue
        # Row groups leave room for the section heading that prefixes each chunk.
        budget = max(chunk_size - len(heading) - 2, chunk_size // 2) if heading else chunk_size
        row_group: List[str] = []
        for row in rows:
            tentative = "\n".join([header, *row_group, row]).strip()
            if row_group and len(tentative) > budget:
                units.append(("table_part", "\n".join([header, *row_group]).strip()))
                row_group = [row]
            else:
                row_group.append(row)
        if row_group:
            units.append(("table_part", "\n".join([header, *row_group]).strip()))

    chunks = _pack_units(units, document_id, chunk_size, chunk_overlap)
    elapsed = time.perf_counter() - started
    return chunks, elapsed


def chunk_document(
    document: Any,
    document_id: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> Tuple[List[Chunk], str, float]:
    from .doclang_loader import document_to_markdown

    markdown = document_to_markdown(document)
    chunks, elapsed = chunk_markdown(
        markdown, document_id, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return chunks, markdown, elapsed
