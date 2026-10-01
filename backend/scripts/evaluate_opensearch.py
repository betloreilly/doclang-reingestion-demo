"""Evaluate FinanceBench questions via OpenSearch k-NN and print hit rates."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.recall.evaluate_opensearch import evaluate_opensearch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-documents", type=int, default=None)
    parser.add_argument("--save", action="store_true", help="Write a recall run JSON.")
    args = parser.parse_args()

    result = evaluate_opensearch(max_documents=args.max_documents)
    for key, p in result["pipelines"].items():
        g = p["retrieval"]["global"]["overall"]
        d = p["retrieval"]["per_document"]["overall"]
        print(
            f"{p['label']:<28} global hit@5 {g['hit_at']['5']:.1%} recall@5 {g['recall_at']['5']:.1%}  "
            f"per-doc hit@5 {d['hit_at']['5']:.1%} recall@5 {d['recall_at']['5']:.1%}  index={p.get('index')}"
        )
    if args.save:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        result["run_id"] = run_id
        result["created_at"] = now
        result["finished_at"] = now
        path = get_settings().recall_dir / "runs" / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2))
        print(f"\nSaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
