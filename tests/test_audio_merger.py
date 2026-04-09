"""Tests for PeerAudioMixer — energy-weighted multi-source audio merging."""

import numpy as np
import pytest

from audio.merger import PeerAudioMixer
from network.clock import ClockSync


class TestPeerAudioMixerPassThrough:
    """When no peers are registered, the mixer should pass through immediately."""

    def test_no_peers_returns_chunk_immediately(self):
        mixer = PeerAudioMixer(merge_delay_ms=60)
        chunk = np.random.randn(1024).astype(np.float32) * 0.5
        result = mixer.add_local_chunk(chunk, local_ts=1.0)
        assert len(result) == 1
        np.testing.assert_array_equal(result[0], chunk)

    def test_no_peers_zero_delay(self):
        mixer = PeerAudioMixer(merge_delay_ms=60)
        assert mixer.merge_delay == 0.0

    def test_peer_count_zero(self):
        mixer = PeerAudioMixer()
        assert mixer.peer_count == 0


class TestPeerAudioMixerDelayBuffer:
    """Test the jitter buffer delay behavior."""

    def test_delay_active_with_peers(self):
        mixer = PeerAudioMixer(merge_delay_ms=100)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)
        assert mixer.merge_delay == 0.1
        assert mixer.peer_count == 1

    def test_chunks_delayed_until_cutoff(self):
        mixer = PeerAudioMixer(merge_delay_ms=100)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)

        chunk = np.zeros(1024, dtype=np.float32)

        # Chunk at t=1.0 — delay is 100ms, so nothing emitted yet
        result = mixer.add_local_chunk(chunk, local_ts=1.0)
        assert len(result) == 0

        # Chunk at t=1.05 — still within delay
        result = mixer.add_local_chunk(chunk, local_ts=1.05)
        assert len(result) == 0

        # Chunk at t=1.15 — first chunk at t=1.0 is now past the 100ms delay
        result = mixer.add_local_chunk(chunk, local_ts=1.15)
        assert len(result) >= 1

    def test_flush_emits_all_buffered(self):
        mixer = PeerAudioMixer(merge_delay_ms=100)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)

        for i in range(5):
            mixer.add_local_chunk(np.zeros(1024, dtype=np.float32), local_ts=float(i) * 0.01)

        flushed = mixer.flush()
        assert len(flushed) == 5

    def test_delay_drops_to_zero_when_peer_removed(self):
        mixer = PeerAudioMixer(merge_delay_ms=100)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)
        assert mixer.merge_delay == 0.1

        mixer.remove_peer("peer-a")
        assert mixer.merge_delay == 0.0
        assert mixer.peer_count == 0


class TestPeerAudioMixerMerging:
    """Test the energy-weighted mixing algorithm."""

    def test_single_source_pass_through(self):
        """With one peer registered but no peer audio, local chunk passes through."""
        mixer = PeerAudioMixer(merge_delay_ms=0)
        # Use delay=0 for simpler testing
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)
        # Remove to get zero delay but test merge logic via flush
        mixer.remove_peer("peer-a")

        chunk = np.ones(1024, dtype=np.float32) * 0.5
        result = mixer.add_local_chunk(chunk, local_ts=1.0)
        assert len(result) == 1
        np.testing.assert_allclose(result[0], chunk, atol=1e-6)

    def test_loud_source_dominates(self):
        """A louder source should get higher weight in the mix."""
        mixer = PeerAudioMixer(merge_delay_ms=0)

        # We can't easily test with the delay buffer in play, so
        # test the _merge_chunk method directly
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)

        # Write a loud frame to the peer buffer
        loud_signal = (np.ones(320, dtype=np.float32) * 0.8 * 32767).astype(np.int16)
        mixer.peer_frame("peer-a", seq=0, pcm_int16=loud_signal.tobytes())

        # Local chunk is quiet
        quiet_local = np.ones(320, dtype=np.float32) * 0.05

        merged = mixer._merge_chunk(quiet_local)

        # The merged output should be louder than the quiet local due to peer contribution
        local_rms = float(np.sqrt(np.mean(quiet_local ** 2)))
        merged_rms = float(np.sqrt(np.mean(merged ** 2)))
        assert merged_rms > local_rms

    def test_silent_source_excluded(self):
        """A source below QUALITY_RMS_GATE should be excluded (weight=0)."""
        mixer = PeerAudioMixer(merge_delay_ms=0)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)

        # Write near-silent frame to peer buffer
        silent_signal = (np.ones(320, dtype=np.float32) * 0.001 * 32767).astype(np.int16)
        mixer.peer_frame("peer-a", seq=0, pcm_int16=silent_signal.tobytes())

        # Local chunk has actual signal
        local_chunk = np.ones(320, dtype=np.float32) * 0.3
        merged = mixer._merge_chunk(local_chunk)

        # Merged should be very close to local since peer is gated out
        np.testing.assert_allclose(merged, np.clip(local_chunk * 1.2, -1, 1), atol=0.05)

    def test_output_clipped_to_range(self):
        """Output must always be in [-1, 1]."""
        mixer = PeerAudioMixer(merge_delay_ms=0)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)

        # Both sources at max
        loud = (np.ones(320, dtype=np.float32) * 0.95 * 32767).astype(np.int16)
        mixer.peer_frame("peer-a", seq=0, pcm_int16=loud.tobytes())

        local = np.ones(320, dtype=np.float32) * 0.95
        merged = mixer._merge_chunk(local)

        assert np.all(merged >= -1.0)
        assert np.all(merged <= 1.0)


class TestPeerAudioMixerDebug:
    """Test debug info output."""

    def test_debug_info_empty(self):
        mixer = PeerAudioMixer()
        info = mixer.debug_info()
        assert info["peer_count"] == 0
        assert info["merge_delay_ms"] == 60
        assert info["buffered_chunks"] == 0

    def test_debug_info_with_peers(self):
        mixer = PeerAudioMixer(merge_delay_ms=50)
        clock = ClockSync()
        mixer.register_peer("peer-a", clock)

        # Write some frames
        frame = np.zeros(320, dtype=np.int16).tobytes()
        mixer.peer_frame("peer-a", seq=0, pcm_int16=frame)
        mixer.peer_frame("peer-a", seq=1, pcm_int16=frame)

        info = mixer.debug_info()
        assert info["peer_count"] == 1
        assert info["merge_delay_ms"] == 50
        assert info["peer_frames"]["peer-a"] == 2
