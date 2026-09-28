"""Ingest cached FinanceBench recall embeddings into local OpenSearch.

Prerequisites:
  docker compose up -d   # from repo root
  A completed (non-estimate) recall run so embeddings exist under data/recall/embeddings/

Examples:
  .venv/bin/python scripts/ingest_recall_opensearch.py
  .venv/bin/python scripts/ingest_recall_opensearch.py --max-documents 3
  .venv/bin/python scripts/evaluate_opensearch.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.recall.ingest import IngestRequest, ingest_pipelines  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--chunk-overlap", type=int, default=150)
    parser.add_argument("--max-documents", type=int, default=None)
    parser.add_argument("--keep-indexes", action="store_true", help="Do not recreate indexes.")
    args = parser.parse_args()

    def progress(stage: str, value: float, message: str) -> None:
        print(f"[{stage} {value:.0%}] {message}", flush=True)

    result = ingest_pipelines(
        IngestRequest(
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            recreate_indexes=not args.keep_indexes,
            max_documents=args.max_documents,
        ),
        on_progress=progress,
    )
    print()
    for pipeline, info in result["pipelines"].items():
        print(
            f"{pipeline}: index={info['index']} chunks={info['chunks']} "
            f"errors={info['errors']} skipped_docs={info['skipped_documents']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
