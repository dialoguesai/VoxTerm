"""Tests for the LocalAgreement transcription pipeline."""

import pytest
from transcriber.agreement import AgreementState, _norm, _strip_prefix


class TestNorm:
    def test_basic(self):
        assert _norm("Hello,") == "hello"
        assert _norm("  World!  ") == "world"
        assert _norm("test.") == "test"

    def test_preserves_interior(self):
        assert _norm("don't") == "don't"


class TestStripPrefix:
    def test_matching_prefix(self):
        assert _strip_prefix(["a", "b", "c"], ["a", "b"]) == ["c"]

    def test_no_match(self):
        assert _strip_prefix(["x", "y"], ["a", "b"]) == ["x", "y"]

    def test_empty_prefix(self):
        assert _strip_prefix(["a", "b"], []) == ["a", "b"]

    def test_prefix_longer_than_words(self):
        assert _strip_prefix(["a"], ["a", "b", "c"]) == ["a"]


class TestAgreementState:
    def test_first_tick_commits_nothing(self):
        s = AgreementState()
        committed, pending = s.tick("Hello world")
        assert committed == ""
        assert pending == "Hello world"

    def test_second_tick_commits_common_prefix(self):
        s = AgreementState()
        s.tick("Hello world how are you")
        committed, pending = s.tick("Hello world what is up")
        assert committed == "Hello world"
        assert pending == "what is up"

    def test_retranscribed_prefix_stripped(self):
        """After committing 'Hello world', if the transcriber re-produces
        it due to approximate trimming, it should be stripped."""
        s = AgreementState()
        s.tick("Hello world how are you")
        s.tick("Hello world what is up")
        # Re-transcribes committed prefix + new content
        committed, pending = s.tick("Hello world what is up today")
        assert committed == "what is up"
        assert pending == "today"

    def test_clean_trim_no_prefix(self):
        """After good trimming, only new words come through."""
        s = AgreementState()
        s.tick("Hello world how are you")
        s.tick("Hello world what is up")
        s.tick("what is up today")
        committed, pending = s.tick("today friends")
        assert committed == "today"
        assert pending == "friends"

    def test_flush_all(self):
        s = AgreementState()
        s.tick("one two three")
        s.tick("one two four five")
        flushed = s.flush_all()
        assert "four five" in flushed
        assert s.committed_text == "one two four five"

    def test_flush_empty(self):
        s = AgreementState()
        assert s.flush_all() == ""

    def test_reset(self):
        s = AgreementState()
        s.tick("Hello world")
        s.tick("Hello world foo")
        s.reset()
        assert s.committed_text == ""
        assert not s.has_hypothesis

    def test_full_pipeline(self):
        """Simulate a realistic sequence of overlapping transcriptions."""
        s = AgreementState()

        s.tick("The quick brown")
        c1, _ = s.tick("The quick brown fox jumps")
        assert c1 == "The quick brown"

        c2, _ = s.tick("The quick brown fox jumps over")
        assert c2 == "fox jumps"

        flush = s.flush_all()
        assert flush == "over"
        assert s.committed_text == "The quick brown fox jumps over"

    def test_no_agreement_diverging(self):
        """Completely different hypotheses → nothing committed."""
        s = AgreementState()
        s.tick("alpha beta gamma")
        committed, pending = s.tick("delta epsilon zeta")
        assert committed == ""
        assert pending == "delta epsilon zeta"

    def test_has_hypothesis(self):
        s = AgreementState()
        assert not s.has_hypothesis
        s.tick("hello world")
        assert s.has_hypothesis
        s.flush_all()
        assert not s.has_hypothesis

    def test_empty_ticks(self):
        s = AgreementState()
        c, p = s.tick("")
        assert c == "" and p == ""
        c, p = s.tick("  ")
        assert c == "" and p == ""

    def test_punctuation_insensitive(self):
        """Agreement should match regardless of trailing punctuation."""
        s = AgreementState()
        s.tick("Hello, world.")
        committed, pending = s.tick("Hello world!")
        assert committed == "Hello world!"

    def test_get_trim_seconds(self):
        s = AgreementState()
        assert s.get_trim_seconds(5.0) == 0.0

        s.tick("one two three four")
        s.tick("one two five six")
        # After committing "one two", trim should be positive
        trim = s.get_trim_seconds(4.0)
        assert trim >= 0.0

    def test_get_trim_seconds_full_commit(self):
        """When a tick fully commits the hypothesis (no pending words),
        trimming should still advance the buffer."""
        s = AgreementState()
        s.tick("alpha beta gamma")
        committed, pending = s.tick("alpha beta gamma")
        assert committed == "alpha beta gamma"
        assert pending == ""
        trim = s.get_trim_seconds(3.0)
        assert trim > 0.0
