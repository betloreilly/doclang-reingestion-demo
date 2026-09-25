"""Token counting: local (Qwen) vs paid-provider estimates."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence, Tuple

PaidMethod = Literal[
    "tiktoken_cl100k", "tiktoken_o200k", "chars_div_4", "same_as_local"
]


@dataclass
class TokenCountResult:
    token_count: int
    tokenizer_name: str
    method: str
    exact: bool
    truncated: bool = False
    truncation_note: Optional[str] = None
    max_length: Optional[int] = None


class LocalTokenizer:
    """Lazy-loaded Qwen embedding tokenizer."""

    def __init__(self, model_name: str = "Qwen/Qwen3-Embedding-0.6B") -> None:
        self.model_name = model_name
        self._tokenizer = None
        self.max_length = 8192  # documented default for Qwen3-Embedding-0.6B

    def _ensure(self):
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name, padding_side="left", trust_remote_code=True
            )
            model_max = getattr(self._tokenizer, "model_max_length", None)
            if isinstance(model_max, int) and 0 < model_max < 100_000:
                self.max_length = model_max
        return self._tokenizer

    def count(self, text: str) -> TokenCountResult:
        tokenizer = self._ensure()
        encoded = tokenizer(
            text,
            add_special_tokens=True,
            truncation=False,
            return_attention_mask=False,
        )
        ids = encoded["input_ids"]
        n = len(ids)
        truncated = n > self.max_length
        note = None
        if truncated:
            note = (
                f"Input length {n} exceeds model max_length {self.max_length}. "
                "Content would be truncated during embedding; surfaced rather than discarded silently."
            )
        return TokenCountResult(
            token_count=n,
            tokenizer_name=self.model_name,
            method="transformers_auto_tokenizer_exact",
            exact=True,
            truncated=truncated,
            truncation_note=note,
            max_length=self.max_length,
        )


def count_paid_tokens(
    text: str,
    method: PaidMethod,
    local_count: Optional[int] = None,
    model_label: str = "",
) -> TokenCountResult:
    if method == "same_as_local":
        if local_count is None:
            raise ValueError("local_count required for same_as_local")
        return TokenCountResult(
            token_count=local_count,
            tokenizer_name=model_label or "proxy:same_as_local_qwen",
            method="same_as_local_qwen_proxy",
            exact=False,
        )
    if method == "chars_div_4":
        approx = max(1, (len(text) + 3) // 4) if text else 0
        return TokenCountResult(
            token_count=approx,
            tokenizer_name="approx:chars/4",
            method="chars_div_4_approximate",
            exact=False,
        )
    try:
        import tiktoken

        enc_name = "cl100k_base" if method == "tiktoken_cl100k" else "o200k_base"
        enc = tiktoken.get_encoding(enc_name)
        n = len(enc.encode(text))
        return TokenCountResult(
            token_count=n,
            tokenizer_name=f"tiktoken:{enc_name}",
            method=f"tiktoken_{enc_name}_exact_for_encoding",
            exact=True,
        )
    except Exception as exc:  # noqa: BLE001
        approx = max(1, (len(text) + 3) // 4) if text else 0
        return TokenCountResult(
            token_count=approx,
            tokenizer_name="approx:chars/4",
            method=f"fallback_chars_div_4_after_error:{type(exc).__name__}",
            exact=False,
        )


def count_chunks(
    texts: Sequence[str],
    local: LocalTokenizer,
    paid_method: PaidMethod,
    paid_model_label: str = "",
) -> Tuple[List[TokenCountResult], List[TokenCountResult], float]:
    started = time.perf_counter()
    local_results: List[TokenCountResult] = []
    paid_results: List[TokenCountResult] = []
    for text in texts:
        loc = local.count(text)
        local_results.append(loc)
        paid_results.append(
            count_paid_tokens(
                text,
                paid_method,
                local_count=loc.token_count,
                model_label=paid_model_label,
            )
        )
    elapsed = time.perf_counter() - started
    return local_results, paid_results, elapsed
