"""Pluggable local-LLM summarizer.

Backends:
  - ``mlx``  : on-device MLX chat model via ``mlx-lm`` (default on macOS)

The factory ``get_summarizer()`` reads ConfigStore for the active model.
Backends are loaded lazily — importing this module does not load any LLM.
"""

from __future__ import annotations

import sys
import threading
from typing import Protocol

from .prompts import Template


def _truncate_for_context(text: str, max_chars: int) -> str:
    """Bound transcript size so it can't overflow the model context window.

    Keeps the start and the most recent tail (where conclusions/action items
    usually land), eliding the middle with a visible marker so the model —
    and anyone reading the input — knows content was dropped.
    """
    if len(text) <= max_chars:
        return text
    marker = "\n\n[… transcript truncated for length — middle omitted …]\n\n"
    budget = max_chars - len(marker)
    if budget <= 0:
        return text[:max_chars]
    head = int(budget * 0.6)
    tail = budget - head
    return text[:head] + marker + text[-tail:]


class SummarizerError(RuntimeError):
    """Raised when summarization can't be performed (missing backend, load failure, etc.)."""


class Summarizer(Protocol):
    """A local-LLM summarizer."""

    def summarize(self, transcript: str, template: Template, custom_prompt: str = "") -> str:
        ...


# ---------------------------------------------------------------------------
# MLX backend
# ---------------------------------------------------------------------------

class MLXSummarizer:
    """MLX chat-model summarizer for Apple Silicon.

    Loads ``mlx_lm`` lazily on first use. The model is cached on the instance,
    so repeated summarize() calls reuse the loaded weights.
    """

    DEFAULT_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"
    # ~4 chars/token heuristic. 48k chars ≈ 12k tokens of input, leaving
    # ample headroom for the prompt + response on a 32k-context model and
    # capping peak memory even on smaller-context models.
    DEFAULT_MAX_INPUT_CHARS = 48_000

    def __init__(
        self,
        model_name: str = "",
        max_tokens: int = 800,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
    ):
        self._model_name = model_name or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._max_input_chars = max_input_chars
        self._model = None
        self._tokenizer = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from mlx_lm import load  # type: ignore
        except ImportError as e:
            raise SummarizerError(
                "mlx-lm not installed. Install with: pip install mlx-lm"
            ) from e
        try:
            self._model, self._tokenizer = load(self._model_name)
        except Exception as e:
            raise SummarizerError(
                f"Failed to load MLX model '{self._model_name}': {e}"
            ) from e

    def summarize(
        self, transcript: str, template: Template, custom_prompt: str = ""
    ) -> str:
        self._load()
        from mlx_lm import generate  # type: ignore

        transcript = _truncate_for_context(transcript, self._max_input_chars)
        user_msg = template.user.format(
            transcript=transcript, custom=custom_prompt or ""
        )
        messages = [
            {"role": "system", "content": template.system},
            {"role": "user", "content": user_msg},
        ]
        prompt = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        try:
            text = generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=self._max_tokens,
                verbose=False,
            )
        except Exception as e:
            raise SummarizerError(f"MLX generation failed: {e}") from e
        return text.strip()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, Summarizer] = {}


def get_summarizer(model_name: str = "") -> Summarizer:
    """Return a (cached) Summarizer for the current platform.

    Instances are cached by model name so the loaded weights survive across
    repeated invocations — pressing the summarize key again reuses the
    already-loaded model instead of reloading from scratch.

    Currently only MLX is supported (macOS). Future: HTTP backend for
    ollama / llama.cpp servers, controlled by a backend prefix in
    ``model_name`` (e.g. ``http://localhost:11434/...``).
    """
    if sys.platform != "darwin":
        raise SummarizerError(
            "Summarization is currently only supported on macOS (MLX). "
            "Linux/Windows backends are not yet implemented."
        )
    key = model_name or MLXSummarizer.DEFAULT_MODEL
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            cached = MLXSummarizer(model_name=model_name)
            _cache[key] = cached
        return cached
