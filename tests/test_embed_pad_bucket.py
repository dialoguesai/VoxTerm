"""Tests for speaker-embedding Fbank frame-count bucketing (ONNX arena fix)."""

import numpy as np
import pytest

from audio.diarization.onnx_embedder import _pad_fbank_to_bucket


@pytest.fixture(autouse=True)
def _default_grid(monkeypatch):
    # Pin the grid so tests don't depend on the ambient env var.
    monkeypatch.setenv("VOXTERM_EMBED_PAD_FRAMES", "100")


def _feats(n_frames: int, n_mels: int = 80) -> np.ndarray:
    # Distinct last row so we can verify replicate-last padding.
    rng = np.random.default_rng(0)
    f = rng.standard_normal((n_frames, n_mels)).astype(np.float32)
    f[-1] = 42.0
    return f


def test_rounds_up_to_next_grid():
    feats = _feats(148)
    out = _pad_fbank_to_bucket(feats)
    assert out.shape == (200, 80)


def test_exact_grid_length_unchanged():
    feats = _feats(200)
    out = _pad_fbank_to_bucket(feats)
    assert out.shape == (200, 80)
    assert out is feats  # no copy when already aligned


def test_padding_replicates_last_frame_and_preserves_signal():
    feats = _feats(110)
    out = _pad_fbank_to_bucket(feats)
    assert np.array_equal(out[:110], feats)
    # Replicate-last (NOT zero-padding) — the pool needs in-distribution rows.
    assert np.all(out[110:] == 42.0)


def test_dtype_preserved():
    feats = _feats(110)
    assert _pad_fbank_to_bucket(feats).dtype == np.float32


def test_variable_lengths_collapse_to_few_shapes():
    # The whole point: many distinct frame counts -> a tiny set of shapes.
    rng = np.random.default_rng(0)
    shapes = set()
    for _ in range(200):
        n = int(rng.integers(98, 301))  # ~1s..3s worth of frames
        shapes.add(_pad_fbank_to_bucket(_feats(n)).shape[0])
    assert shapes <= {100, 200, 300}


def test_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("VOXTERM_EMBED_PAD_FRAMES", "0")
    feats = _feats(148)
    out = _pad_fbank_to_bucket(feats)
    assert out is feats


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VOXTERM_EMBED_PAD_FRAMES", "not-a-number")
    feats = _feats(148)
    assert _pad_fbank_to_bucket(feats).shape[0] == 200


@pytest.mark.parametrize("bad", ["-1", "-100"])
def test_negative_env_disables(monkeypatch, bad):
    monkeypatch.setenv("VOXTERM_EMBED_PAD_FRAMES", bad)
    feats = _feats(148)
    out = _pad_fbank_to_bucket(feats)
    assert out is feats


def test_empty_feats_unchanged():
    feats = np.zeros((0, 80), dtype=np.float32)
    out = _pad_fbank_to_bucket(feats)
    assert out.shape == (0, 80)
