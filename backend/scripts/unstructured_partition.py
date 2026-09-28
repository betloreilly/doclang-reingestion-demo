"""Partition PDFs with the open-source Unstructured library.

Runs inside backend/.venv-unstructured so its dependencies stay separate from the
app venv. Reads a JSON list of {"pdf": path, "out": path, "strategy"?: str} from
stdin and writes one JSON file of elements per PDF. Prints one JSON status line
per document.

Strategies: fast (default, text layer only) | hi_res (layout model + tables).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    from unstructured.__version__ import __version__ as unstructured_version
    from unstructured.partition.pdf import partition_pdf

    jobs = json.load(sys.stdin)
    failures = 0
    for job in jobs:
        pdf = Path(job["pdf"])
        out = Path(job["out"])
        strategy = (job.get("strategy") or "fast").strip().lower()
        if strategy not in {"fast", "hi_res", "ocr_only", "auto"}:
            print(
                json.dumps(
                    {
                        "pdf": pdf.name,
                        "ok": False,
                        "error": f"Unsupported strategy: {strategy}",
                    }
                ),
                flush=True,
            )
            failures += 1
            continue
        started = time.perf_counter()
        try:
            kwargs = {
                "filename": str(pdf),
                "strategy": strategy,
                "languages": ["eng"],
            }
            if strategy == "hi_res":
                kwargs["infer_table_structure"] = True
            elements = partition_pdf(**kwargs)
            payload = {
                "source": pdf.name,
                "strategy": strategy,
                "unstructured_version": unstructured_version,
                "elements": [
                    {
                        "type": el.category,
                        "text": el.text,
                        "page_number": el.metadata.page_number,
                    }
                    for el in elements
                    if el.text and el.text.strip()
                ],
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(out.suffix + ".tmp")
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, out)
            status = {
                "pdf": pdf.name,
                "ok": True,
                "strategy": strategy,
                "elements": len(payload["elements"]),
            }
        except Exception as exc:  # noqa: BLE001
            failures += 1
            status = {
                "pdf": pdf.name,
                "ok": False,
                "strategy": strategy,
                "error": f"{type(exc).__name__}: {exc}",
            }
        status["seconds"] = round(time.perf_counter() - started, 2)
        print(json.dumps(status), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
