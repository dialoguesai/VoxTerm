"""Tests for CrossModelTranscriber ROVER merge and alignment logic."""

import pytest

from transcriber.engine import CrossModelTranscriber, _ALLOWED_MODEL_TYPES


@pytest.fixture
def transcriber():
    """Create a CrossModelTranscriber without loading models (for unit-testing merge logic)."""
    t = CrossModelTranscriber.__new__(CrossModelTranscriber)
    t._models = []
    t._loaded = False
    t._init_dedup()
    t._stats = {
        "all_agree": 0, "majority_vote": 0, "rescued": 0,
        "all_empty": 0, "fallback_primary": 0, "total": 0,
    }
    return t


class TestAlignPair:

    def test_identical_sequences(self, transcriber):
        words = ["hello", "world"]
        aligned = transcriber._align_pair(words, words)
        assert aligned == [("hello", "hello"), ("world", "world")]

    def test_substitution(self, transcriber):
        a = ["the", "cat", "sat"]
        b = ["the", "bat", "sat"]
        aligned = transcriber._align_pair(a, b)
        assert len(aligned) == 3
        assert aligned[0] == ("the", "the")
        assert aligned[1] == ("cat", "bat")  # substitution
        assert aligned[2] == ("sat", "sat")

    def test_insertion(self, transcriber):
        a = ["hello", "world"]
        b = ["hello", "big", "world"]
        aligned = transcriber._align_pair(a, b)
        # Should have 3 pairs: one insertion for "big"
        words_a = [p[0] for p in aligned if p[0] is not None]
        words_b = [p[1] for p in aligned if p[1] is not None]
        assert words_a == ["hello", "world"]
        assert words_b == ["hello", "big", "world"]

    def test_deletion(self, transcriber):
        a = ["hello", "big", "world"]
        b = ["hello", "world"]
        aligned = transcriber._align_pair(a, b)
        words_a = [p[0] for p in aligned if p[0] is not None]
        words_b = [p[1] for p in aligned if p[1] is not None]
        assert words_a == ["hello", "big", "world"]
        assert words_b == ["hello", "world"]

    def test_empty_sequences(self, transcriber):
        assert transcriber._align_pair([], []) == []
        aligned = transcriber._align_pair(["hello"], [])
        assert aligned == [("hello", None)]
        aligned = transcriber._align_pair([], ["hello"])
        assert aligned == [(None, "hello")]


class TestRoverMerge:

    def test_identical_texts(self, transcriber):
        texts = ["hello world", "hello world"]
        merged, disagreed = transcriber._rover_merge(texts)
        assert merged == "hello world"
        assert disagreed is False

    def test_two_model_substitution(self, transcriber):
        texts = ["the cat sat", "the bat sat"]
        merged, disagreed = transcriber._rover_merge(texts)
        assert disagreed is True
        # Both have equal votes, but backbone (first) wins ties
        assert "the" in merged
        assert "sat" in merged

    def test_three_model_majority_vote(self, transcriber):
        texts = ["the cat sat", "the dog sat", "the cat sat"]
        merged, disagreed = transcriber._rover_merge(texts)
        assert disagreed is True
        assert merged == "the cat sat"  # 2 out of 3 agree on "cat"

    def test_three_model_insertion_indexing(self, transcriber):
        """Regression test: insertions from model 2 must not corrupt model 3's alignment."""
        texts = [
            "hello world",         # backbone
            "hello big world",     # insertion of "big"
            "hello world",         # same as backbone
        ]
        merged, _ = transcriber._rover_merge(texts)
        # "big" has 1 vote, epsilon has 2 votes → "big" should be dropped
        assert merged == "hello world"

    def test_three_model_insertion_accepted(self, transcriber):
        """When majority inserts a word, it should appear in output."""
        texts = [
            "hello world",
            "hello big world",
            "hello big world",
        ]
        merged, _ = transcriber._rover_merge(texts)
        # "big" has 2 votes vs epsilon's 1 → "big" should be kept
        assert merged == "hello big world"

    def test_deletion_by_majority(self, transcriber):
        texts = ["the big cat", "the cat", "the cat"]
        merged, disagreed = transcriber._rover_merge(texts)
        assert disagreed is True
        # 2 out of 3 delete "big" → should be dropped
        assert merged == "the cat"

    def test_single_text(self, transcriber):
        merged, disagreed = transcriber._rover_merge(["hello world"])
        assert merged == "hello world"
        assert disagreed is False

    def test_empty_list(self, transcriber):
        merged, disagreed = transcriber._rover_merge([])
        assert merged == ""
        assert disagreed is False

    def test_preserves_raw_casing(self, transcriber):
        texts = ["Hello World", "hello world"]
        merged, _ = transcriber._rover_merge(texts)
        # Backbone's raw casing should be preserved
        assert merged == "Hello World"


class TestModelTypeValidation:

    def test_valid_types_accepted(self):
        # Should not raise
        CrossModelTranscriber(
            primary_type="qwen3", secondary_type="whisper",
        )

    def test_invalid_primary_type_raises(self):
        with pytest.raises(ValueError, match="primary_type"):
            CrossModelTranscriber(primary_type="gpt4", secondary_type="whisper")

    def test_invalid_secondary_type_raises(self):
        with pytest.raises(ValueError, match="secondary_type"):
            CrossModelTranscriber(primary_type="qwen3", secondary_type="deepseek")

    def test_invalid_tertiary_type_raises(self):
        with pytest.raises(ValueError, match="tertiary_type"):
            CrossModelTranscriber(
                tertiary_model="some-model", tertiary_type="invalid",
            )

    def test_allowed_model_types_constant(self):
        assert _ALLOWED_MODEL_TYPES == {"qwen3", "whisper"}


class TestResetDedup:

    def test_reset_dedup_clears_state(self, transcriber):
        transcriber._recent = ["foo", "bar"]
        transcriber.reset_dedup()
        assert transcriber._recent == []
