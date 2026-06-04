"""Partial-hypothesis stabilizer for live transcription (LocalAgreement-n).

A streaming transcriber re-decodes the still-growing speech tail on every pass, so the
raw partial text keeps rewriting itself as more audio arrives. This stabilizes the
display: it commits the longest leading run of words that has agreed across the last
``n`` passes (the *stable* prefix) and marks only the trailing remainder of the newest
hypothesis as *volatile*. As a word stays put for ``n`` consecutive passes it graduates
from volatile to stable, so the head stops flickering while the tail keeps updating in
near-real-time.

LocalAgreement-n is the standard streaming-ASR commit policy (n=2 is the common default).
The idea is ported from elizaOS's streaming partial-stabilizer; reimplemented here for
VoxTerm (no model needed — it operates on the transcriber's own incremental output).
"""
from __future__ import annotations

from collections import deque


def common_prefix_len(seqs: list[list[str]]) -> int:
    """Length of the longest leading run of words shared by ALL ``seqs``."""
    if not seqs:
        return 0
    shortest = min(len(s) for s in seqs)
    for i in range(shortest):
        w = seqs[0][i]
        if any(s[i] != w for s in seqs):
            return i
    return shortest


class PartialStabilizer:
    """Commit the leading words that agree across the last ``n`` partial hypotheses.

    Call :meth:`push` with each new raw partial; it returns ``{"stable", "volatile"}``.
    Call :meth:`reset` when the current utterance is finalized so the next partial starts
    clean. Whitespace is the token boundary (good enough for live display; the saved
    transcript still comes from the full post-stop pipeline).
    """

    def __init__(self, n: int = 2):
        self.n = max(2, int(n))
        self._hist: deque[list[str]] = deque(maxlen=self.n)

    def push(self, text: str) -> dict:
        words = (text or "").split()
        self._hist.append(words)
        # Need a full window before committing anything; until then it's all volatile.
        stable_len = common_prefix_len(list(self._hist)) if len(self._hist) >= self.n else 0
        return {"stable": " ".join(words[:stable_len]),
                "volatile": " ".join(words[stable_len:])}

    def reset(self) -> None:
        self._hist.clear()
