"""Tests for gui.stabilize.PartialStabilizer (LocalAgreement-n).

Pure logic — no audio, no model. Verifies the commit policy: a leading word run only
becomes "stable" once it has agreed across the last n hypotheses; the tail stays
"volatile"; reset() clears the window. Pytest-style; also runnable standalone.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (str(_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from stabilize import PartialStabilizer, common_prefix_len


def test_common_prefix_len_basic():
    assert common_prefix_len([["a", "b", "c"], ["a", "b", "d"]]) == 2
    assert common_prefix_len([["a", "b"], ["x", "y"]]) == 0
    assert common_prefix_len([["a", "b", "c"], ["a", "b", "c"]]) == 3


def test_common_prefix_len_edge():
    assert common_prefix_len([]) == 0
    assert common_prefix_len([["a", "b"]]) == 2          # single seq → all shared
    assert common_prefix_len([["a"], []]) == 0           # one empty → nothing shared


def test_first_push_is_all_volatile():
    # n=2 default: with only one hypothesis there is nothing to agree against.
    s = PartialStabilizer()
    out = s.push("hello there friend")
    assert out["stable"] == ""
    assert out["volatile"] == "hello there friend"


def test_agreement_commits_prefix():
    s = PartialStabilizer()
    s.push("the quick brown")
    out = s.push("the quick brown fox")
    # "the quick brown" agreed across both → stable; "fox" is new → volatile.
    assert out["stable"] == "the quick brown"
    assert out["volatile"] == "fox"


def test_divergent_tail_stays_volatile():
    s = PartialStabilizer()
    s.push("i think we should")
    out = s.push("i think we shall not")
    # prefix "i think we" agrees; "should" vs "shall" diverge → volatile from there.
    assert out["stable"] == "i think we"
    assert out["volatile"] == "shall not"


def test_reset_clears_window():
    s = PartialStabilizer()
    s.push("alpha beta")
    s.push("alpha beta")            # would commit "alpha beta"
    s.reset()
    out = s.push("gamma delta")     # fresh window → all volatile again
    assert out["stable"] == ""
    assert out["volatile"] == "gamma delta"


def test_empty_text():
    s = PartialStabilizer()
    out = s.push("")
    assert out == {"stable": "", "volatile": ""}
    out = s.push("   ")             # whitespace-only → no tokens
    assert out == {"stable": "", "volatile": ""}


def test_n_three_needs_three_agreeing():
    s = PartialStabilizer(n=3)
    assert s.push("a b")["stable"] == ""        # 1 hyp
    assert s.push("a b")["stable"] == ""        # 2 hyps, n=3 not reached
    assert s.push("a b")["stable"] == "a b"     # 3 hyps agree → commit


def test_shrinking_hypothesis_does_not_overcommit():
    # If the newest hypothesis is shorter, stable can only be as long as the shortest.
    s = PartialStabilizer()
    s.push("one two three four")
    out = s.push("one two")
    assert out["stable"] == "one two"
    assert out["volatile"] == ""


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
