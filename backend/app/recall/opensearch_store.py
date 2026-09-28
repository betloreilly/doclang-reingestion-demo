"""OpenSearch client for FinanceBench recall indexes (k-NN + page metadata)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from opensearchpy.exceptions import ConnectionError as OSConnectionError
from opensearchpy.exceptions import TransportError

from ..config import Settings, get_settings

SNIPPET_CHARS = 280
BULK_CHUNK = 200


def get_client(settings: Optional[Settings] = None) -> OpenSearch:
    s = settings or get_settings()
    return OpenSearch(
        hosts=[s.opensearch_url],
        http_auth=(s.opensearch_user, s.opensearch_password.get_secret_value()),
        use_ssl=s.opensearch_url.lower().startswith("https"),
        verify_certs=s.opensearch_verify_certs,
        ssl_show_warn=False,
        connection_class=RequestsHttpConnection,
        timeout=60,
        max_retries=3,
        retry_on_timeout=True,
    )


def connection_status(settings: Optional[Settings] = None) -> Dict[str, Any]:
    s = settings or get_settings()
    payload: Dict[str, Any] = {
        "configured": True,
        "url": s.opensearch_url,
        "index_prefix": s.opensearch_index_prefix,
        "connected": False,
        "cluster_name": None,
        "version": None,
        "indexes": {},
        "error": None,
    }
    try:
        client = get_client(s)
        info = client.info()
        payload["connected"] = True
        payload["cluster_name"] = info.get("cluster_name")
        payload["version"] = (info.get("version") or {}).get("number")
        for pipeline in ("doclang", "unstructured"):
            name = s.opensearch_index(pipeline)
            exists = client.indices.exists(index=name)
            count = int(client.count(index=name)["count"]) if exists else 0
            payload["indexes"][pipeline] = {
                "name": name,
                "exists": bool(exists),
                "docs": count,
            }
    except (OSConnectionError, TransportError, Exception) as exc:  # noqa: BLE001
        payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload


def index_body(dimension: int) -> Dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.knn": True,
        },
        "mappings": {
            "properties": {
                "pipeline": {"type": "keyword"},
                "doc_name": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "page": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "text": {"type": "text"},
                "embedding_model": {"type": "keyword"},
                "chunk_size": {"type": "integer"},
                "chunk_overlap": {"type": "integer"},
                "ingested_at": {"type": "date"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dimension,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "lucene",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
            }
        },
    }


def ensure_index(
    pipeline: str,
    dimension: int,
    settings: Optional[Settings] = None,
    recreate: bool = False,
) -> str:
    s = settings or get_settings()
    client = get_client(s)
    name = s.opensearch_index(pipeline)
    exists = client.indices.exists(index=name)
    if exists and recreate:
        client.indices.delete(index=name)
        exists = False
    if not exists:
        client.indices.create(index=name, body=index_body(dimension))
    return name


def bulk_index_chunks(
    pipeline: str,
    docs: Iterable[Dict[str, Any]],
    settings: Optional[Settings] = None,
) -> Tuple[int, int]:
    """Bulk-index chunk documents. Returns (success_count, error_count)."""
    s = settings or get_settings()
    client = get_client(s)
    index = s.opensearch_index(pipeline)

    def actions():
        for doc in docs:
            yield {
                "_op_type": "index",
                "_index": index,
                "_id": doc["chunk_id"],
                "_source": doc,
            }

    success, errors = helpers.bulk(
        client,
        actions(),
        chunk_size=BULK_CHUNK,
        raise_on_error=False,
        request_timeout=120,
    )
    err_count = len(errors) if isinstance(errors, list) else int(errors or 0)
    client.indices.refresh(index=index)
    return int(success), err_count


def knn_search(
    pipeline: str,
    vector: Sequence[float],
    k: int = 10,
    doc_name: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> List[Dict[str, Any]]:
    """Return top-k hits with doc_name, page, score, snippet (no embedding in source)."""
    s = settings or get_settings()
    client = get_client(s)
    index = s.opensearch_index(pipeline)
    knn: Dict[str, Any] = {
        "embedding": {
            "vector": list(vector),
            "k": k if doc_name is None else max(k * 5, 50),
        }
    }
    if doc_name:
        knn["embedding"]["filter"] = {"term": {"doc_name": doc_name}}
    body = {
        "size": k,
        "_source": {"excludes": ["embedding"]},
        "query": {"knn": knn},
    }
    response = client.search(index=index, body=body)
    hits = []
    for hit in response.get("hits", {}).get("hits", []):
        src = hit.get("_source") or {}
        text = src.get("text") or ""
        hits.append(
            {
                "doc_name": src.get("doc_name"),
                "page": src.get("page"),
                "score": float(hit.get("_score") or 0.0),
                "chunk_id": src.get("chunk_id"),
                "snippet": text[:SNIPPET_CHARS],
                "relevant": False,
            }
        )
    return hits[:k]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
