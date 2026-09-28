"""Run the FinanceBench recall comparison from the command line.

Examples (from backend/):
  .venv/bin/python scripts/run_recall.py --estimate
  .venv/bin/python scripts/run_recall.py --max-documents 5
  .venv/bin/python scripts/run_recall.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.recall.runner import RecallRequest, get_recall_manager  # noqa: E402
from app.schemas import JobStatus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--estimate", action="store_true", help="Count tokens and cost; no OpenAI calls.")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--chunk-overlap", type=int, default=150)
    parser.add_argument("--max-documents", type=int, default=None)
    args = parser.parse_args()

    manager = get_recall_manager()
    job = manager.start(
        RecallRequest(
            estimate_only=args.estimate,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            max_documents=args.max_documents,
        )
    )
    printed = 0
    while job.status in (JobStatus.queued, JobStatus.running):
        time.sleep(1)
        for line in job.logs[printed:]:
            print(line, flush=True)
        printed = len(job.logs)
    for line in job.logs[printed:]:
        print(line, flush=True)
    if job.status != JobStatus.completed:
        print(f"Recall run failed: {job.error}", file=sys.stderr)
        return 1

    run = json.loads(manager.run_path(job.run_id).read_text())
    emb = run["embedding"]
    print(f"\nRun {run['run_id']} · {run['dataset']['questions_evaluated']} questions · {run['dataset']['documents_evaluated']} docs")
    print(f"Embedding tokens {emb['tokens_total']:,} (≈ ${emb['estimated_cost_full_usd']:.2f}); billed this run ${emb['cost_this_run_usd']:.2f}")
    for key, p in run["pipelines"].items():
        cov = p["evidence_coverage"]
        line = f"{p['label']:<28} chunks {p['chunks']:>7,}  evidence coverage {cov['mean']:.1%}"
        if p["retrieval"]:
            g = p["retrieval"]["global"]["overall"]
            d = p["retrieval"]["per_document"]["overall"]
            line += "  global hit@5 {:.1%} @10 {:.1%}  per-doc hit@5 {:.1%}".format(
                g["hit_at"]["5"], g["hit_at"]["10"], d["hit_at"]["5"]
            )
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
