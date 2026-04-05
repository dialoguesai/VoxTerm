"""Tests for diarization/fbank.py — pure-numpy Mel filterbank extraction."""

import numpy as np
import pytest

from audio.diarization.fbank import compute_fbank


class TestComputeFbank:

    def test_output_shape(self):
        """Output should be (num_frames, 80) for standard parameters."""
        audio = np.random.randn(32000).astype(np.float32) * 0.1  # 2 seconds
        feats = compute_fbank(audio, sample_rate=16000)
        assert feats.ndim == 2
        assert feats.shape[1] == 80
        # 2s at 10ms shift = ~198 frames (minus edge effects)
        assert 190 <= feats.shape[0] <= 200

    def test_empty_audio(self):
        """Very short audio should return empty features."""
        audio = np.zeros(100, dtype=np.float32)
        feats = compute_fbank(audio, sample_rate=16000)
        assert feats.shape[0] == 0
        assert feats.shape[1] == 80

    def test_silence(self):
        """Silence should produce finite features (no NaN/Inf)."""
        audio = np.zeros(24000, dtype=np.float32)
        feats = compute_fbank(audio, sample_rate=16000)
        assert np.all(np.isfinite(feats))

    def test_cmn(self):
        """With CMN enabled, features should be zero-mean per bin."""
        audio = np.random.randn(48000).astype(np.float32) * 0.1
        feats = compute_fbank(audio, sample_rate=16000, cmn=True)
        mean_per_bin = feats.mean(axis=0)
        assert np.allclose(mean_per_bin, 0.0, atol=1e-5)

    def test_no_cmn(self):
        """With CMN disabled, features should NOT be zero-mean."""
        audio = np.random.randn(48000).astype(np.float32) * 0.1
        feats = compute_fbank(audio, sample_rate=16000, cmn=False)
        mean_per_bin = feats.mean(axis=0)
        # At least some bins should have non-zero mean
        assert np.any(np.abs(mean_per_bin) > 0.1)

    def test_deterministic(self):
        """Same input should produce same output."""
        audio = np.random.RandomState(42).randn(24000).astype(np.float32) * 0.1
        feats1 = compute_fbank(audio, sample_rate=16000)
        feats2 = compute_fbank(audio, sample_rate=16000)
        assert np.array_equal(feats1, feats2)

    def test_different_mel_bins(self):
        """Should support different numbers of mel bins."""
        audio = np.random.randn(24000).astype(np.float32) * 0.1
        feats_40 = compute_fbank(audio, num_mel_bins=40)
        feats_80 = compute_fbank(audio, num_mel_bins=80)
        assert feats_40.shape[1] == 40
        assert feats_80.shape[1] == 80

    def test_mono_float32(self):
        """Output dtype should be float32."""
        audio = np.random.randn(24000).astype(np.float32) * 0.1
        feats = compute_fbank(audio, sample_rate=16000)
        assert feats.dtype == np.float32
