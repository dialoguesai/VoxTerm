"""Tests for gui.eot — the heuristic end-of-turn classifier (pure, no model)."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (str(_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from eot import turn_complete_prob, is_incomplete


def test_terminal_punctuation_is_complete():
    for t in ("I went to the store.", "Really?", "Stop!"):
        assert turn_complete_prob(t) == 0.95
        assert not is_incomplete(t)


def test_trailing_conjunction_is_incomplete():
    for t in ("I went to the store and", "we could do that but", "I stayed because"):
        assert turn_complete_prob(t) == 0.15
        assert is_incomplete(t)


def test_trailing_article_or_preposition_is_incomplete():
    for t in ("I put it on the", "let's talk about", "give it to"):
        assert turn_complete_prob(t) == 0.20
        assert is_incomplete(t)


def test_short_fragment_is_complete_enough():
    # < 3 words, no terminal punctuation, not a dangling function word
    assert turn_complete_prob("okay sure") == 0.70
    assert not is_incomplete("okay sure")


def test_normal_midlength_clause_is_neutral_complete():
    assert turn_complete_prob("i think we should go now") == 0.50
    assert not is_incomplete("i think we should go now")


def test_empty_and_punct_only():
    assert turn_complete_prob("") == 0.5
    assert turn_complete_prob("   ") == 0.5
    assert not is_incomplete("")


def test_punctuation_beats_dangling_word():
    # a period wins even if the last word would otherwise be a conjunction
    assert turn_complete_prob("we can stop. and") == 0.15      # truly ends on "and"
    assert turn_complete_prob("first this and that.") == 0.95  # ends with period


def test_case_insensitive():
    assert turn_complete_prob("I WENT AND") == 0.15
    assert is_incomplete("I WENT AND")


def test_threshold_boundary():
    # default threshold 0.4: 0.20 and 0.15 are incomplete; 0.50/0.70/0.95 are not
    assert is_incomplete("on the")          # 0.20
    assert not is_incomplete("okay sure")   # 0.70
    assert not is_incomplete("we should go now then")  # 0.50


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
