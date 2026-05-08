"""LocalAgreement — overlapping-chunk transcription with consensus commit.

Adapted from vbuterin/stt-daemon's LocalAgreement algorithm (whisper-streaming,
Maciej Pióro, UFAL). Each transcription tick produces a hypothesis; a word is
committed to output only when two consecutive ticks agree on it at the front of
the hypothesis. The audio buffer is trimmed to just after the last committed
word so the transcription window always starts from known-good ground.

This eliminates hallucinated tail words and produces smoother, more accurate
real-time output than the fire-and-forget approach.
"""

from __future__ import annotations


def _norm(word: str) -> str:
    """Normalize a word for comparison: lowercase, strip punctuation."""
    return word.strip().lower().rstrip(".,!?;:")


def _strip_prefix(words: list[str], prefix: list[str]) -> list[str]:
    """If `words` starts with `prefix` (normalized match), strip it."""
    if not prefix or len(prefix) > len(words):
        return words
    for i, pw in enumerate(prefix):
        if _norm(words[i]) != _norm(pw):
            return words  # no match
    return words[len(prefix):]


class AgreementState:
    """Tracks state for the LocalAgreement algorithm across transcription ticks.

    Since audio trimming is approximate, the transcriber may re-produce words
    that were already committed. This class strips the known committed tail
    from new transcriptions and runs agreement only on the uncommitted portion.

    State:
        _committed_words: All words that have been agreed upon and flushed.
        _hypothesis: The UNCOMMITTED tail from the previous tick — only words
                     beyond what was committed, used for next-tick comparison.
        _overlap_ref: Recent committed words used to detect and strip
                      re-transcribed prefixes from new output.
    """

    def __init__(self):
        self._committed_words: list[str] = []
        self._hypothesis: list[str] = []
        self._committed_time: float = 0.0
        self._last_flushed: int = 0
        # Sliding window of recent committed words for prefix detection
        self._overlap_ref: list[str] = []

    def _strip_retranscribed(self, words: list[str]) -> list[str]:
        """Remove re-transcribed committed words from the front of new output.

        Tries progressively shorter suffixes of the overlap reference to find
        the longest one that matches a prefix of `words`.
        """
        if not self._overlap_ref or not words:
            return words

        ref = self._overlap_ref
        best_skip = 0

        # Try each suffix of the reference (shortest to longest would be
        # less efficient; go longest-first for the best match)
        for length in range(min(len(ref), len(words)), 0, -1):
            suffix = ref[-length:]
            match = all(
                _norm(suffix[j]) == _norm(words[j])
                for j in range(length)
            )
            if match:
                best_skip = length
                break

        return words[best_skip:]

    def tick(self, new_text: str) -> tuple[str, str]:
        """Process a new transcription result.

        Args:
            new_text: Transcription text from the current tick.

        Returns:
            (newly_committed_text, pending_text) where:
              - newly_committed_text: new words committed this tick
              - pending_text: current unconfirmed hypothesis
        """
        raw_words = new_text.split() if new_text.strip() else []

        # Strip any re-transcribed committed prefix
        new_words = self._strip_retranscribed(raw_words)

        # Compare with previous hypothesis (both are relative to commit point)
        committed: list[str] = []
        i = 0
        while i < len(self._hypothesis) and i < len(new_words):
            if _norm(self._hypothesis[i]) == _norm(new_words[i]):
                committed.append(new_words[i])
                i += 1
            else:
                break

        if committed:
            self._committed_words.extend(committed)
            self._overlap_ref.extend(committed)
            if len(self._overlap_ref) > 30:
                self._overlap_ref = self._overlap_ref[-20:]

        # Store only the UNCOMMITTED tail as hypothesis for next tick
        self._hypothesis = new_words[len(committed):]

        # Newly committed text since last flush
        new_committed_text = " ".join(
            self._committed_words[self._last_flushed:]
        )
        self._last_flushed = len(self._committed_words)

        pending_text = " ".join(self._hypothesis)

        return new_committed_text, pending_text

    def get_trim_seconds(self, audio_duration: float) -> float:
        """Calculate how many seconds to trim from audio buffer front.

        Estimates based on the ratio of committed vs total words, with a
        safety margin to avoid trimming into uncommitted speech. When the
        hypothesis is fully committed (no pending words), trims most of
        the buffer to avoid re-transcribing committed audio.
        """
        if not self._overlap_ref:
            return 0.0

        n_committed = len(self._overlap_ref)
        n_pending = len(self._hypothesis)
        total = n_committed + n_pending

        if n_pending == 0:
            # Fully committed — trim most of the buffer, keep 0.5s safety margin
            return max(0.0, audio_duration - 0.5)

        committed_fraction = n_committed / total
        trim_to = max(0.0, audio_duration * committed_fraction - 0.5)
        return trim_to

    def flush_all(self) -> str:
        """Force-commit all remaining hypothesis words (e.g., on silence/end).

        Returns all text that hasn't been flushed to the UI yet.
        """
        if self._hypothesis:
            self._committed_words.extend(self._hypothesis)
            self._hypothesis = []

        text = " ".join(self._committed_words[self._last_flushed:])
        self._last_flushed = len(self._committed_words)
        return text

    def reset(self):
        """Reset all state (e.g., between recording sessions)."""
        self._committed_words.clear()
        self._hypothesis.clear()
        self._committed_time = 0.0
        self._last_flushed = 0
        self._overlap_ref.clear()

    @property
    def committed_time(self) -> float:
        return self._committed_time

    @committed_time.setter
    def committed_time(self, value: float):
        self._committed_time = value

    @property
    def has_hypothesis(self) -> bool:
        return bool(self._hypothesis)

    @property
    def committed_text(self) -> str:
        return " ".join(self._committed_words)
