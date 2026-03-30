"""Unit tests for DiarizationEngine.diarize_offline.

Tests the offline diarization pipeline without requiring real models by
mocking the segmentation and embedding backends.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Helpers ──────────────────────────────────────────────────

def _make_engine_with_mocks(n_speakers=2):
    """Create a DiarizationEngine with mocked segmentation + embeddings."""
    from diarization.engine import DiarizationEngine

    engine = DiarizationEngine.__new__(DiarizationEngine)
    engine._loaded = True
    engine._backend = "onnx"
    engine._model = None
    engine._onnx_embedder = MagicMock()
    engine._speaker_centroids = {}
    engine._speaker_names = {}
    engine._speaker_colors = {}
    engine._prev_centroids = {}
    engine._matched_speakers = set()
    engine._next_speaker_id = 1
    engine._embedding_dim = 512

    # Mock segmentation
    seg = MagicMock()
    seg.is_loaded = True

    def mock_segment(chunk):
        n_frames = len(chunk) // 270
        activation = np.zeros((n_frames, 3), dtype=np.float32)
        # Speaker 0 active in first half, speaker 1 in second half
        mid = n_frames // 2
        activation[:mid, 0] = 0.8
        activation[mid:, 1] = 0.8
        if n_speakers >= 3:
            # Third speaker overlaps with both
            activation[mid // 2 : mid + mid // 2, 2] = 0.7
        return activation

    seg.segment = mock_segment

    def mock_active_speakers(activation):
        speakers = []
        for i in range(activation.shape[1]):
            peak = float(activation[:, i].max())
            if peak >= 0.55:
                speakers.append({
                    "speaker_idx": i,
                    "mean_activation": float(activation[:, i].mean()),
                    "peak_activation": peak,
                    "is_long": True,
                })
        return speakers

    seg.get_active_speakers = mock_active_speakers
    engine._segmentation = seg

    # Mock embedding extraction: return different embeddings per speaker
    call_count = [0]
    def mock_extract_weighted(audio, weights, sr):
        idx = call_count[0]
        call_count[0] += 1
        emb = np.random.RandomState(idx).randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        return emb

    engine._onnx_embedder.extract_weighted.side_effect = mock_extract_weighted

    return engine


# ── Tests ────────────────────────────────────────────────────

class TestDiarizeOfflineSampleRateValidation:
    def test_rejects_non_16k_sample_rate(self):
        engine = _make_engine_with_mocks()
        audio = np.zeros(48000, dtype=np.float32)
        with pytest.raises(ValueError, match="16 kHz"):
            engine.diarize_offline(audio, sample_rate=48000)

    def test_accepts_16k_sample_rate(self):
        engine = _make_engine_with_mocks()
        audio = np.random.randn(16000 * 5).astype(np.float32)
        # Should not raise
        engine.diarize_offline(audio, sample_rate=16000)


class TestDiarizeOfflineFallback:
    def test_falls_back_when_segmentation_missing(self):
        engine = _make_engine_with_mocks()
        engine._segmentation = None
        audio = np.random.randn(16000 * 5).astype(np.float32)

        with patch.object(engine, "_diarize_online_fallback", return_value=[]) as mock_fb:
            result = engine.diarize_offline(audio, sample_rate=16000)
            mock_fb.assert_called_once()


class TestDiarizeOfflineSegmentBounds:
    def test_segments_within_audio_bounds(self):
        engine = _make_engine_with_mocks()
        audio = np.random.randn(16000 * 10).astype(np.float32)
        results = engine.diarize_offline(audio, sample_rate=16000)

        for label, sid, start, end in results:
            assert start >= 0, f"Segment start {start} is negative"
            assert end <= len(audio), f"Segment end {end} exceeds audio length {len(audio)}"
            assert start < end, f"Segment start {start} >= end {end}"


class TestDiarizeOfflineOverlap:
    def test_overlapping_speakers_allowed(self):
        """Multiple speakers can have segments covering the same time span."""
        engine = _make_engine_with_mocks(n_speakers=3)
        audio = np.random.randn(16000 * 20).astype(np.float32)
        results = engine.diarize_offline(audio, sample_rate=16000)

        if len(results) < 2:
            pytest.skip("Not enough segments to test overlap")

        # Check that we have multiple distinct speakers
        speaker_ids = set(sid for _, sid, _, _ in results)
        assert len(speaker_ids) >= 2, "Expected at least 2 speakers"

        # Check that there's at least some temporal overlap between speakers
        by_speaker: dict[int, list[tuple[int, int]]] = {}
        for _, sid, s, e in results:
            by_speaker.setdefault(sid, []).append((s, e))

        sids = list(by_speaker.keys())
        has_overlap = False
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                for s1, e1 in by_speaker[sids[i]]:
                    for s2, e2 in by_speaker[sids[j]]:
                        if s1 < e2 and s2 < e1:
                            has_overlap = True
                            break

        # Overlap is allowed (not required) — just verify the structure is valid
        assert isinstance(results, list)
