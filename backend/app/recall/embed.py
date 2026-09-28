"""OpenAI embeddings with a per-document on-disk cache. The API key is never logged."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import numpy as np

MAX_INPUT_TOKENS = 8191
MAX_BATCH_INPUTS = 512
MAX_BATCH_TOKENS = 250_000

_KEY_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_\-*]{4,}")
# Providers echo keys masked as "abcd****wxyz"; drop those fragments entirely.
_MASKED_KEY_RE = re.compile(r"[A-Za-z0-9_\-]*\*{3,}[A-Za-z0-9_\-]*")
_KEY_ECHO_RE = re.compile(r"(Incorrect API key provided:)[^.,'\"}]*", re.IGNORECASE)


def texts_hash(model: str, texts: Sequence[str]) -> str:
    digest = hashlib.sha256(model.encode())
    for text in texts:
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()


class TokenCounter:
    """cl100k_base, the tokenizer used by text-embedding-3-* models."""

    def __init__(self) -> None:
        import tiktoken

        self.encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        return len(self.encoding.encode(text, disallowed_special=()))

    def truncate(self, text: str) -> str:
        ids = self.encoding.encode(text, disallowed_special=())
        if len(ids) <= MAX_INPUT_TOKENS:
            return text
        return self.encoding.decode(ids[:MAX_INPUT_TOKENS])


def load_cached(path: Path, expected_hash: str) -> Optional[np.ndarray]:
    if not path.is_file():
        return None
    with np.load(path) as data:
        if str(data["hash"]) != expected_hash:
            return None
        return data["vectors"].astype(np.float32)


def save_cached(path: Path, vectors: np.ndarray, content_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez(tmp, vectors=vectors.astype(np.float16), hash=np.array(content_hash))
    tmp.replace(path)


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, base_url: str = "") -> None:
        from openai import OpenAI

        self._secret = api_key
        kwargs = {"api_key": api_key, "max_retries": 6, "timeout": 120.0}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.tokens = TokenCounter()

    def _scrub(self, message: str) -> str:
        if self._secret:
            message = message.replace(self._secret, "***")
            for size in (8, 6, 4):
                for fragment in (self._secret[:size], self._secret[-size:]):
                    if len(self._secret) > 2 * size:
                        message = message.replace(fragment, "***")
        message = _KEY_ECHO_RE.sub(r"\1 ***", message)
        message = _MASKED_KEY_RE.sub("***", message)
        return _KEY_LIKE_RE.sub("sk-***", message)

    def embed(
        self,
        texts: Sequence[str],
        on_batch: Optional[Callable[[int], None]] = None,
    ) -> np.ndarray:
        """Return L2-normalized float32 vectors, one per input text."""
        prepared = [self.tokens.truncate(t) for t in texts]
        counts = [self.tokens.count(t) for t in prepared]
        out: List[Optional[List[float]]] = [None] * len(prepared)

        start = 0
        while start < len(prepared):
            end, budget = start, 0
            while (
                end < len(prepared)
                and end - start < MAX_BATCH_INPUTS
                and (end == start or budget + counts[end] <= MAX_BATCH_TOKENS)
            ):
                budget += counts[end]
                end += 1
            try:
                response = self.client.embeddings.create(
                    model=self.model, input=prepared[start:end], encoding_format="float"
                )
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "AuthenticationError":
                    raise RuntimeError(
                        "OpenAI rejected OPENAI_API_KEY (401). Check the key in backend/.env; "
                        "keys for Azure OpenAI or a gateway also need OPENAI_BASE_URL."
                    ) from None
                raise RuntimeError(
                    f"OpenAI embeddings request failed: {type(exc).__name__}: "
                    f"{self._scrub(str(exc))}"
                ) from None
            for item in response.data:
                out[start + item.index] = item.embedding
            if on_batch:
                on_batch(end - start)
            start = end

        vectors = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)
