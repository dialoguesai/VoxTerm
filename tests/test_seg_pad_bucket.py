"""Tests for segmentation raw-sample bucketing (ONNX-arena fragmentation fix)."""

import numpy as np
import pytest

from audio.diarization.segmentation import _SEG_SR, _pad_samples_to_bucket


@pytest.fixture(autouse=True)
def _default_grid(monkeypatch):
    # Pin the grid so tests don't depend on the ambient env var.
    monkeypatch.setenv("VOXTERM_SEG_PAD_SAMPLES", str(_SEG_SR))


def test_rounds_up_to_next_second():
    audio = np.ones(int(1.4 * _SEG_SR), dtype=np.float32)
    out = _pad_samples_to_bucket(audio)
    assert len(out) == 2 * _SEG_SR


def test_exact_grid_length_unchanged():
    audio = np.ones(2 * _SEG_SR, dtype=np.float32)
    out = _pad_samples_to_bucket(audio)
    assert len(out) == 2 * _SEG_SR
    assert out is audio  # no copy when already aligned


def test_padding_is_trailing_silence_and_preserves_signal():
    audio = np.ones(int(1.1 * _SEG_SR), dtype=np.float32)
    out = _pad_samples_to_bucket(audio)
    assert np.array_equal(out[: len(audio)], audio)
    assert np.all(out[len(audio):] == 0.0)


def test_dtype_preserved():
    audio = np.ones(int(1.1 * _SEG_SR), dtype=np.float32)
    assert _pad_samples_to_bucket(audio).dtype == np.float32


def test_variable_lengths_collapse_to_few_shapes():
    # The whole point: many distinct input lengths -> a tiny set of shapes.
    rng = np.random.default_rng(0)
    shapes = set()
    for _ in range(200):
        n = int(rng.integers(_SEG_SR, 3 * _SEG_SR))  # 1s..3s
        shapes.add(len(_pad_samples_to_bucket(np.zeros(n, dtype=np.float32))))
    assert shapes <= {1 * _SEG_SR, 2 * _SEG_SR, 3 * _SEG_SR}


def test_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("VOXTERM_SEG_PAD_SAMPLES", "0")
    audio = np.ones(int(1.4 * _SEG_SR), dtype=np.float32)
    out = _pad_samples_to_bucket(audio)
    assert out is audio


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VOXTERM_SEG_PAD_SAMPLES", "not-a-number")
    audio = np.ones(int(1.4 * _SEG_SR), dtype=np.float32)
    assert len(_pad_samples_to_bucket(audio)) == 2 * _SEG_SR


@pytest.mark.parametrize("bad", ["-1", "-16000"])
def test_negative_env_disables(monkeypatch, bad):
    monkeypatch.setenv("VOXTERM_SEG_PAD_SAMPLES", bad)
    audio = np.ones(int(1.4 * _SEG_SR), dtype=np.float32)
    out = _pad_samples_to_bucket(audio)
    assert out is audio


def test_empty_audio_unchanged():
    audio = np.zeros(0, dtype=np.float32)
    out = _pad_samples_to_bucket(audio)
    assert len(out) == 0


def test_segment_trims_padding_frames(monkeypatch):
    """segment() must run inference on bucketed audio but return frames
    proportional to the ORIGINAL duration, so padding never reaches the
    activation stats."""
    from audio.diarization.segmentation import SpeakerSegmentation

    seg = SpeakerSegmentation.__new__(SpeakerSegmentation)
    seg._loaded = True

    captured = {}

    class _FakeSession:
        def run(self, _outputs, feeds):
            n = feeds["input_values"].shape[-1]
            captured["padded_n"] = n
            frames = n // 270  # model emits ~1 frame / 270 samples
            return [np.zeros((1, frames, 7), dtype=np.float32)]

    seg._session = _FakeSession()

    orig_n = int(1.4 * _SEG_SR)
    activation = seg.segment(np.ones(orig_n, dtype=np.float32))

    # Inference ran on the padded (2s) buffer...
    assert captured["padded_n"] == 2 * _SEG_SR
    # ...but the returned activation is trimmed back to ~the 1.4s duration.
    full_frames = (2 * _SEG_SR) // 270
    expected = round(full_frames * orig_n / (2 * _SEG_SR))
    assert abs(activation.shape[0] - expected) <= 1
    assert activation.shape[1] == 3
