"""FinanceBench open-source questions (patronus-ai/financebench)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import httpx

FINANCEBENCH_REPO = "https://github.com/patronus-ai/financebench"
QUESTIONS_URL = (
    "https://raw.githubusercontent.com/patronus-ai/financebench/main/data/"
    "financebench_open_source.jsonl"
)
QUESTIONS_FILE = "financebench_open_source.jsonl"


@dataclass(frozen=True)
class Evidence:
    doc_name: str
    page: int  # 1-based, same numbering as DocLang prov.page_no and Unstructured page_number
    text: str


@dataclass
class Question:
    financebench_id: str
    doc_name: str
    question_type: str
    question: str
    answer: str
    evidence: List[Evidence] = field(default_factory=list)

    def evidence_pages(self) -> set[tuple[str, int]]:
        return {(e.doc_name, e.page) for e in self.evidence}


def ensure_dataset(dataset_dir: Path) -> Path:
    """Return the local questions file, downloading it once over verified TLS."""
    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / QUESTIONS_FILE
    if path.is_file() and path.stat().st_size > 0:
        return path
    response = httpx.get(QUESTIONS_URL, timeout=60, follow_redirects=True)
    response.raise_for_status()
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(response.content)
    os.replace(tmp, path)
    return path


def parse_question(row: dict) -> Question:
    evidence = [
        Evidence(
            doc_name=ev.get("doc_name") or row["doc_name"],
            # FinanceBench evidence_page_num is zero-indexed.
            page=int(ev["evidence_page_num"]) + 1,
            text=ev.get("evidence_text") or "",
        )
        for ev in row.get("evidence") or []
    ]
    return Question(
        financebench_id=row["financebench_id"],
        doc_name=row["doc_name"],
        question_type=row.get("question_type") or "unknown",
        question=row["question"],
        answer=str(row.get("answer") or ""),
        evidence=evidence,
    )


def load_questions(path: Path) -> List[Question]:
    questions: List[Question] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                questions.append(parse_question(json.loads(line)))
    return questions
