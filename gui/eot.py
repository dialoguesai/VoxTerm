"""Heuristic end-of-turn (EOT) signal from transcript text — zero model, zero latency.

VoxTerm splits turns on silence alone, so a natural pause after "and…" or "the…" wrongly
ends a turn mid-sentence. This estimates P(the text is a grammatically complete turn) from
cheap string cues, so the live view can MERGE a fragment that clearly ends mid-clause into
the next one instead of emitting two choppy lines.

Reimplemented (idea, not code) from elizaOS's HeuristicEotClassifier. Pure stdlib.
"""
from __future__ import annotations

import re

# A fragment that ENDS on one of these is almost certainly continued by the next one.
_CONJUNCTIONS = {
    "and", "but", "or", "nor", "yet", "so", "because", "although", "though", "while",
    "since", "unless", "until", "whereas", "plus", "that", "which", "who", "whom",
    "whose", "if", "when", "where", "as", "than",
}
_ARTICLES_PREPS = {
    "the", "a", "an", "to", "of", "in", "on", "at", "by", "for", "with", "from", "into",
    "onto", "upon", "about", "over", "under", "between", "among", "through", "during",
    "without", "within", "my", "your", "his", "her", "its", "our", "their",
}

_WORD = re.compile(r"[A-Za-z']+")


def turn_complete_prob(text: str) -> float:
    """Estimate P(this text is a complete turn) in [0,1] from grammar cues alone.

    Terminal sentence punctuation → 0.95; a trailing conjunction → 0.15; a trailing
    article/preposition → 0.20; a very short fragment → 0.70; otherwise 0.50.
    """
    t = (text or "").strip()
    if not t:
        return 0.5
    if t[-1] in ".!?":
        return 0.95
    words = _WORD.findall(t.lower())
    if not words:
        return 0.5
    last = words[-1]
    if last in _CONJUNCTIONS:
        return 0.15
    if last in _ARTICLES_PREPS:
        return 0.20
    if len(words) < 3:
        return 0.70
    return 0.50


def is_incomplete(text: str, threshold: float = 0.4) -> bool:
    """True when the text clearly ends mid-clause — i.e. the NEXT fragment should merge in.

    Only the high-precision cases (trailing conjunction/article/preposition) fall below the
    default threshold, so this never merges across a genuinely-complete sentence.
    """
    return turn_complete_prob(text) < threshold
