"""Discover and pair DocLang / PDF / JSON objects by stem."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple

from .schemas import DocumentPair, ObjectInfo


def _basename(key: str) -> str:
    return PurePosixPath(key).name


def strip_known_suffixes(name: str) -> str:
    """Normalize compound suffixes such as .pdf.dclx → stem."""
    lower = name.lower()
    for suffix in (
        ".pdf.dclx",
        ".dclx",
        ".pdf.json",
        ".json",
        ".pdf",
    ):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    # Fallback: drop final extension
    if "." in name:
        return name.rsplit(".", 1)[0]
    return name


def classify_object(obj: ObjectInfo) -> Optional[str]:
    key_lower = obj.key.lower()
    name = _basename(key_lower)
    in_dclx = "/dclx/" in key_lower or key_lower.startswith("dclx/")
    in_pdf = "/pdf/" in key_lower or key_lower.startswith("pdf/")
    in_json = "/json/" in key_lower or key_lower.startswith("json/")

    if name.endswith(".dclx") or (in_dclx and name.endswith(".dclx")):
        return "dclx"
    if name.endswith(".pdf") or (in_pdf and name.endswith(".pdf")):
        return "pdf"
    if name.endswith(".json") or (in_json and name.endswith(".json")):
        return "json"
    # Folder-based fallback without extension mismatches
    if in_dclx:
        return "dclx"
    if in_pdf:
        return "pdf"
    if in_json:
        return "json"
    return None


def _stem_key(obj: ObjectInfo, kind: str) -> str:
    name = _basename(obj.key)
    if kind == "dclx":
        return strip_known_suffixes(name)
    if kind == "pdf":
        return strip_known_suffixes(name)
    if kind == "json":
        return strip_known_suffixes(name)
    return strip_known_suffixes(name)


def pair_objects(objects: List[ObjectInfo]) -> List[DocumentPair]:
    buckets: Dict[str, Dict[str, List[ObjectInfo]]] = defaultdict(
        lambda: {"dclx": [], "pdf": [], "json": []}
    )

    for obj in objects:
        kind = classify_object(obj)
        if not kind:
            continue
        stem = _stem_key(obj, kind)
        # Normalize stem for matching (casefold, collapse whitespace)
        norm = re.sub(r"\s+", " ", stem).strip().casefold()
        buckets[norm][kind].append(obj)

    pairs: List[DocumentPair] = []
    for norm, groups in sorted(buckets.items(), key=lambda x: x[0]):
        dclx_list = groups["dclx"]
        pdf_list = groups["pdf"]
        json_list = groups["json"]

        if not dclx_list:
            # PDF/JSON without DocLang are listed only as unmatched side info —
            # they cannot be selected for processing.
            continue

        # One DocumentPair per dclx; share ambiguous candidates.
        for idx, dclx in enumerate(sorted(dclx_list, key=lambda o: o.key)):
            display = strip_known_suffixes(_basename(dclx.key))
            doc_id = f"{norm}::{idx}" if len(dclx_list) > 1 else norm

            ambiguous_pdf = [o.key for o in pdf_list]
            ambiguous_json = [o.key for o in json_list]

            pdf: Optional[ObjectInfo] = None
            pairing_status = "dclx_only"
            if len(pdf_list) == 1:
                pdf = pdf_list[0]
                pairing_status = "paired"
                ambiguous_pdf = []
            elif len(pdf_list) > 1:
                pairing_status = "ambiguous_pdf"

            json_obj: Optional[ObjectInfo] = None
            if len(json_list) == 1:
                json_obj = json_list[0]
            elif len(json_list) > 1 and pairing_status == "paired":
                pairing_status = "ambiguous_json"

            pairs.append(
                DocumentPair(
                    document_id=doc_id,
                    document_name=display,
                    dclx=dclx,
                    pdf=pdf,
                    json_obj=json_obj,
                    pairing_status=pairing_status,
                    ambiguous_pdf_keys=ambiguous_pdf,
                    ambiguous_json_keys=ambiguous_json,
                )
            )

    return pairs


def apply_manual_pairing(
    pairs: List[DocumentPair],
    document_id: str,
    pdf_key: Optional[str],
    objects_by_key: Dict[str, ObjectInfo],
    page_count: Optional[int] = None,
) -> DocumentPair:
    for pair in pairs:
        if pair.document_id != document_id:
            continue
        if pdf_key:
            if pdf_key not in objects_by_key:
                raise KeyError(f"Unknown PDF key: {pdf_key}")
            pair.pdf = objects_by_key[pdf_key]
            pair.manual_pdf_key = pdf_key
            pair.pairing_status = "manual"
            pair.ambiguous_pdf_keys = [
                k for k in pair.ambiguous_pdf_keys if k != pdf_key
            ]
            if pair.page_count_source != "manual":
                pair.page_count = None
                pair.page_count_source = None
        if page_count is not None:
            pair.page_count = page_count
            pair.page_count_source = "manual"
        return pair
    raise KeyError(f"Unknown document_id: {document_id}")


def index_objects(objects: List[ObjectInfo]) -> Dict[str, ObjectInfo]:
    return {o.key: o for o in objects}
