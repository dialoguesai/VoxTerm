"""Tests for ASR input-length bucketing (Metal fragmentation fix)."""

import numpy as np
import pytest

from audio.transcriber import _ASR_SR, _pad_to_shape_bucket


@pytest.fixture(autouse=True)
def _default_grid(monkeypatch):
    # Pin the grid so tests don't depend on the ambient env var.
    monkeypatch.setenv("VOXTERM_ASR_PAD_SECONDS", "1.0")


def test_rounds_up_to_next_second():
    audio = np.ones(int(1.4 * _ASR_SR), dtype=np.float32)
    out = _pad_to_shape_bucket(audio)
    assert len(out) == 2 * _ASR_SR


def test_exact_grid_length_unchanged():
    audio = np.ones(2 * _ASR_SR, dtype=np.float32)
    out = _pad_to_shape_bucket(audio)
    assert len(out) == 2 * _ASR_SR
    assert out is audio  # no copy when already aligned


def test_padding_is_trailing_silence_and_preserves_signal():
    audio = np.ones(int(1.1 * _ASR_SR), dtype=np.float32)
    out = _pad_to_shape_bucket(audio)
    assert np.array_equal(out[: len(audio)], audio)
    assert np.all(out[len(audio):] == 0.0)


def test_dtype_preserved():
    audio = np.ones(int(1.1 * _ASR_SR), dtype=np.float32)
    assert _pad_to_shape_bucket(audio).dtype == np.float32


def test_variable_lengths_collapse_to_few_shapes():
    # The whole point: many distinct input lengths -> a tiny set of shapes.
    rng = np.random.default_rng(0)
    shapes = set()
    for _ in range(200):
        n = int(rng.integers(_ASR_SR, 3 * _ASR_SR))  # 1s..3s
        shapes.add(len(_pad_to_shape_bucket(np.zeros(n, dtype=np.float32))))
    assert shapes <= {1 * _ASR_SR, 2 * _ASR_SR, 3 * _ASR_SR}


def test_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("VOXTERM_ASR_PAD_SECONDS", "0")
    audio = np.ones(int(1.4 * _ASR_SR), dtype=np.float32)
    out = _pad_to_shape_bucket(audio)
    assert out is audio


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VOXTERM_ASR_PAD_SECONDS", "not-a-number")
    audio = np.ones(int(1.4 * _ASR_SR), dtype=np.float32)
    assert len(_pad_to_shape_bucket(audio)) == 2 * _ASR_SR


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-1"])
def test_non_finite_or_negative_env_disables_without_crashing(monkeypatch, bad):
    # float("nan"/"inf") parses without ValueError; round(nan*sr) / int(inf)
    # would crash mid-transcription, so these must short-circuit to no-op.
    monkeypatch.setenv("VOXTERM_ASR_PAD_SECONDS", bad)
    audio = np.ones(int(1.4 * _ASR_SR), dtype=np.float32)
    out = _pad_to_shape_bucket(audio)
    assert out is audio


def test_empty_audio_unchanged():
    audio = np.zeros(0, dtype=np.float32)
    out = _pad_to_shape_bucket(audio)
    assert len(out) == 0
