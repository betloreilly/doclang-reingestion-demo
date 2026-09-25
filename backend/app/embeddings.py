"""Local Sentence Transformers embeddings with Qwen3-Embedding-0.6B."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class EmbeddingRunResult:
    vectors: int
    dimension: int
    elapsed_seconds: float
    truncated_inputs: List[int] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


class EmbeddingService:
    """Singleton-style loader: warm once, exclude init from benchmark timings."""

    _lock = threading.Lock()
    _instance: Optional["EmbeddingService"] = None

    def __init__(self, model_name: str = "Qwen/Qwen3-Embedding-0.6B") -> None:
        self.model_name = model_name
        self._model = None
        self.device = "cpu"
        self.revision: Optional[str] = None
        self.dimension: Optional[int] = None
        self.max_seq_length = 8192
        self.init_seconds: Optional[float] = None
        self.warmup_seconds: Optional[float] = None
        self.ready = False

    @classmethod
    def get(cls, model_name: str = "Qwen/Qwen3-Embedding-0.6B") -> "EmbeddingService":
        with cls._lock:
            if cls._instance is None or cls._instance.model_name != model_name:
                cls._instance = EmbeddingService(model_name)
            return cls._instance

    def ensure_loaded(self, log=None) -> Dict[str, Any]:
        if self.ready and self._model is not None:
            return self.metadata()
        with self._lock:
            if self.ready and self._model is not None:
                return self.metadata()
            import torch
            from sentence_transformers import SentenceTransformer

            if log:
                log(f"Loading embedding model {self.model_name} (excluded from benchmark)…")
            started = time.perf_counter()
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            # Documents: encode WITHOUT query instruct prompt (official convention).
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                tokenizer_kwargs={"padding_side": "left"},
            )
            self.init_seconds = time.perf_counter() - started
            self.max_seq_length = int(
                getattr(self._model, "max_seq_length", self.max_seq_length) or 8192
            )
            # Warm-up (excluded from benchmark)
            warm_start = time.perf_counter()
            _ = self._model.encode(
                ["warmup document for DocLang reingestion demo"],
                batch_size=1,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            self.warmup_seconds = time.perf_counter() - warm_start
            probe = self._model.encode(
                ["dim probe"],
                batch_size=1,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            self.dimension = int(probe.shape[-1])
            try:
                self.revision = getattr(
                    getattr(self._model, "model", None), "config", None
                )
                self.revision = getattr(self.revision, "_name_or_path", None) or self.model_name
            except Exception:  # noqa: BLE001
                self.revision = self.model_name
            self.ready = True
            if log:
                log(
                    f"Model ready on {self.device}; init={self.init_seconds:.2f}s "
                    f"warmup={self.warmup_seconds:.2f}s dim={self.dimension}"
                )
            return self.metadata()

    def metadata(self) -> Dict[str, Any]:
        return {
            "model": self.model_name,
            "revision": self.revision,
            "device": self.device,
            "dimension": self.dimension,
            "max_seq_length": self.max_seq_length,
            "init_seconds": self.init_seconds,
            "warmup_seconds": self.warmup_seconds,
            "document_encoding": "sentence_transformers_encode_without_query_prompt",
            "ready": self.ready,
        }

    def embed_documents(
        self,
        texts: Sequence[str],
        batch_size: int = 8,
        prechecked_truncated: Optional[Sequence[bool]] = None,
        on_progress=None,
    ) -> EmbeddingRunResult:
        self.ensure_loaded()
        assert self._model is not None
        truncated_inputs: List[int] = []
        if prechecked_truncated:
            truncated_inputs = [i for i, t in enumerate(prechecked_truncated) if t]

        text_list = list(texts)
        total = len(text_list)
        started = time.perf_counter()
        vector_batches: List[Any] = []

        if total == 0:
            elapsed = time.perf_counter() - started
            return EmbeddingRunResult(
                vectors=0,
                dimension=self.dimension or 0,
                elapsed_seconds=elapsed,
                truncated_inputs=truncated_inputs,
                meta={**self.metadata(), "batch_size": batch_size, "num_inputs": 0},
            )

        import numpy as np

        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch = text_list[start:end]
            batch_vectors = self._model.encode(
                batch,
                batch_size=len(batch),
                show_progress_bar=False,
                normalize_embeddings=True,
                # Document encoding: do NOT pass prompt_name="query"
            )
            vector_batches.append(batch_vectors)
            if on_progress is not None:
                on_progress(end, total, time.perf_counter() - started)

        vectors = np.vstack(vector_batches) if vector_batches else np.zeros((0, 0))
        elapsed = time.perf_counter() - started
        return EmbeddingRunResult(
            vectors=len(vectors),
            dimension=int(vectors.shape[-1]) if len(vectors) else (self.dimension or 0),
            elapsed_seconds=elapsed,
            truncated_inputs=truncated_inputs,
            meta={
                **self.metadata(),
                "batch_size": batch_size,
                "num_inputs": total,
            },
        )
