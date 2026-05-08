"""Tests for audio/merger.py — PeerAudioMixer."""

import time
import numpy as np
import pytest

from audio.merger import PeerAudioMixer


SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1024  # ~64ms at 16kHz


def _sine_chunk(freq=440.0, amplitude=0.5, samples=CHUNK_SAMPLES):
    """Generate a sine wave chunk."""
    t = np.linspace(0, samples / SAMPLE_RATE, samples, dtype=np.float32)
    return amplitude * np.sin(2 * np.pi * freq * t)


def _silence_chunk(samples=CHUNK_SAMPLES):
    return np.zeros(samples, dtype=np.float32)


def _noise_chunk(amplitude=0.5, samples=CHUNK_SAMPLES, seed=42):
    rng = np.random.RandomState(seed)
    return (rng.randn(samples) * amplitude).astype(np.float32)


class TestPeerAudioMixerNoPeers:
    """When no peers are connected, mixer should pass through unchanged."""

    def test_passthrough_no_peers(self):
        mixer = PeerAudioMixer(merge_delay_ms=60)
        chunk = _sine_chunk()
        result = mixer.add_local_chunk(chunk, time.monotonic())
        # No peers → immediate passthrough
        assert result is not None
        np.testing.assert_array_equal(result, chunk)

    def test_active_peers_zero(self):
        mixer = PeerAudioMixer()
        assert mixer.active_peers == 0


class TestPeerAudioMixerWithPeers:
    """Test energy-weighted merging with peer audio."""

    def test_merge_two_sources(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)  # zero delay for testing
        local = _sine_chunk(freq=440, amplitude=0.5)
        peer = _sine_chunk(freq=880, amplitude=0.3)
        now = time.monotonic()

        # Feed peer audio
        mixer.feed_peer("peer-1", peer, now)

        # Need a tiny delay so peer data is registered
        result = mixer.add_local_chunk(local, now)
        assert result is not None
        assert len(result) == len(local)
        # Result should be different from local (mixed)
        assert not np.allclose(result, local, atol=1e-6)
        # Result should be in [-1, 1]
        assert np.all(result >= -1.0) and np.all(result <= 1.0)

    def test_silent_peer_gets_zero_weight(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)
        local = _sine_chunk(freq=440, amplitude=0.5)
        silent_peer = _silence_chunk()
        now = time.monotonic()

        mixer.feed_peer("peer-1", silent_peer, now)
        result = mixer.add_local_chunk(local, now)

        assert result is not None
        # With silent peer (weight=0), result should be close to local * 1.2 (gentle boost)
        expected = np.clip(local * 1.2, -1.0, 1.0)
        np.testing.assert_allclose(result, expected, atol=1e-5)

    def test_louder_peer_dominates(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)
        local_quiet = _sine_chunk(freq=440, amplitude=0.05)
        peer_loud = _sine_chunk(freq=880, amplitude=0.8)
        now = time.monotonic()

        mixer.feed_peer("peer-1", peer_loud, now)
        result = mixer.add_local_chunk(local_quiet, now)

        assert result is not None
        # The loud peer should dominate — result should correlate more with peer
        local_corr = abs(np.corrcoef(result, local_quiet)[0, 1])
        peer_corr = abs(np.corrcoef(result, peer_loud)[0, 1])
        assert peer_corr > local_corr

    def test_multiple_peers(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)
        local = _sine_chunk(freq=440, amplitude=0.3)
        now = time.monotonic()

        mixer.feed_peer("peer-1", _sine_chunk(freq=880, amplitude=0.4), now)
        mixer.feed_peer("peer-2", _sine_chunk(freq=1320, amplitude=0.2), now)

        assert mixer.active_peers == 2
        result = mixer.add_local_chunk(local, now)
        assert result is not None
        assert len(result) == len(local)


class TestPeerAudioMixerDelay:
    """Test the merge delay buffer behavior."""

    def test_delayed_output(self):
        mixer = PeerAudioMixer(merge_delay_ms=50)
        local = _sine_chunk()
        now = time.monotonic()

        # Feed peer data
        mixer.feed_peer("peer-1", _sine_chunk(freq=880), now)

        # First call should return None (delay not elapsed)
        result = mixer.add_local_chunk(local, now)
        assert result is None

        # Wait for delay to elapse
        time.sleep(0.06)
        # Feed another chunk to trigger release of the buffered one
        result = mixer.add_local_chunk(_sine_chunk(), time.monotonic())
        # The delayed chunk should be released now
        assert result is not None

    def test_flush_releases_all(self):
        mixer = PeerAudioMixer(merge_delay_ms=100)
        now = time.monotonic()

        mixer.feed_peer("peer-1", _sine_chunk(), now)
        mixer.add_local_chunk(_sine_chunk(), now)
        mixer.add_local_chunk(_sine_chunk(), now + 0.01)

        flushed = mixer.flush()
        assert len(flushed) == 2


class TestPeerAudioMixerLifecycle:
    """Test peer add/remove and cleanup."""

    def test_remove_peer(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)
        mixer.feed_peer("peer-1", _sine_chunk(), time.monotonic())
        assert mixer.active_peers == 1

        mixer.remove_peer("peer-1")
        assert mixer.active_peers == 0

    def test_remove_nonexistent_peer(self):
        mixer = PeerAudioMixer()
        mixer.remove_peer("nonexistent")  # should not raise

    def test_remove_last_peer_drains_delayed_chunks(self):
        # Last peer dropping out must release any local chunks still waiting
        # in the merge-delay buffer; otherwise audio is lost.
        mixer = PeerAudioMixer(merge_delay_ms=100)
        now = time.monotonic()
        mixer.feed_peer("peer-1", _sine_chunk(), now)
        # Two chunks queued, delay not elapsed → nothing returned yet
        assert mixer.add_local_chunk(_sine_chunk(), now) is None
        assert mixer.add_local_chunk(_sine_chunk(), now + 0.01) is None

        drained = mixer.remove_peer("peer-1")
        assert len(drained) == 2
        assert mixer.active_peers == 0

    def test_clear(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)
        mixer.feed_peer("peer-1", _sine_chunk(), time.monotonic())
        mixer.add_local_chunk(_sine_chunk(), time.monotonic())
        mixer.clear()

        assert mixer.active_peers == 0
        assert mixer.merge_count == 0

    def test_stats(self):
        mixer = PeerAudioMixer(merge_delay_ms=0)
        now = time.monotonic()
        mixer.feed_peer("peer-1", _sine_chunk(), now)
        mixer.add_local_chunk(_sine_chunk(), now)

        stats = mixer.get_stats()
        assert stats["peer_count"] == 1
        assert stats["merge_count"] == 1
        assert stats["peer_contributions"] == 1
