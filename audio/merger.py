"""Energy-weighted multi-source audio merger for P2P sessions.

Merges local mic/system audio with incoming peer audio streams.
Each source is weighted by RMS energy so the mic closest to the
active speaker dominates the mix.  A configurable merge delay
buffers local audio to allow peer frames time to arrive.
"""

from __future__ import annotations

import collections
import threading
import time

import numpy as np

from config import (
    P2P_AUDIO_QUALITY_GATE,
    P2P_MERGE_DELAY_MS,
    SAMPLE_RATE,
)


class PeerAudioMixer:
    """Merge local audio with time-aligned peer audio using energy weights.

    Usage::

        mixer = PeerAudioMixer()

        # In _process_audio_inner, after mixing mic+sys:
        merged = mixer.add_local_chunk(chunk, time.monotonic())
        if merged is not None:
            audio_buffer.append(merged)

        # When peer audio arrives (AudioStreamer callback):
        mixer.feed_peer(node_id, pcm_float32, timestamp)

        # When peer disconnects:
        mixer.remove_peer(node_id)
    """

    def __init__(self, merge_delay_ms: int = P2P_MERGE_DELAY_MS):
        self._merge_delay_sec = merge_delay_ms / 1000.0
        self._lock = threading.Lock()

        # Local delay buffer: list of (timestamp, chunk) waiting for peer data
        self._local_queue: collections.deque[tuple[float, np.ndarray]] = collections.deque()

        # Per-peer recent audio: node_id → deque of (timestamp, chunk)
        # Chunks are float32 [-1, 1], timestamps are local clock (already adjusted)
        self._peer_buffers: dict[str, collections.deque[tuple[float, np.ndarray]]] = {}

        # Stats
        self.merge_count: int = 0
        self.peer_contributions: int = 0  # chunks where ≥1 peer contributed

    @property
    def merge_delay_sec(self) -> float:
        return self._merge_delay_sec

    @property
    def active_peers(self) -> int:
        with self._lock:
            return len(self._peer_buffers)

    def feed_peer(self, node_id: str, pcm_float32: np.ndarray, local_timestamp: float) -> None:
        """Feed a chunk from a peer (timestamp already adjusted to local clock)."""
        with self._lock:
            if node_id not in self._peer_buffers:
                self._peer_buffers[node_id] = collections.deque(maxsize=500) if hasattr(collections.deque, 'maxsize') else collections.deque()
            buf = self._peer_buffers[node_id]
            buf.append((local_timestamp, pcm_float32))
            # Evict old entries (keep ~2s worth at 50 chunks/sec)
            while len(buf) > 200:
                buf.popleft()

    def remove_peer(self, node_id: str) -> None:
        """Remove a peer's buffer when they disconnect."""
        with self._lock:
            self._peer_buffers.pop(node_id, None)

    def add_local_chunk(self, chunk: np.ndarray, timestamp: float) -> np.ndarray | None:
        """Buffer a local chunk and return merged audio when delay has elapsed.

        Returns None if the chunk is still waiting in the delay buffer.
        Returns the energy-weighted merged chunk once the delay has passed.
        When no peers are connected, returns the chunk immediately (zero delay).
        """
        with self._lock:
            has_peers = bool(self._peer_buffers)

        # No peers → zero delay, pass through unchanged
        if not has_peers:
            return chunk

        with self._lock:
            self._local_queue.append((timestamp, chunk))

        # Release chunks whose delay has elapsed
        now = time.monotonic()
        merged_chunks = []

        with self._lock:
            while self._local_queue:
                ts, local_chunk = self._local_queue[0]
                if now - ts < self._merge_delay_sec:
                    break
                self._local_queue.popleft()
                merged = self._merge_with_peers(local_chunk, ts)
                merged_chunks.append(merged)

        if not merged_chunks:
            return None
        return np.concatenate(merged_chunks) if len(merged_chunks) > 1 else merged_chunks[0]

    def flush(self) -> list[np.ndarray]:
        """Flush all remaining buffered chunks (e.g. on session end).

        Returns list of merged chunks.
        """
        result = []
        with self._lock:
            while self._local_queue:
                ts, local_chunk = self._local_queue.popleft()
                result.append(self._merge_with_peers(local_chunk, ts))
        return result

    def clear(self) -> None:
        """Reset all state."""
        with self._lock:
            self._local_queue.clear()
            self._peer_buffers.clear()
            self.merge_count = 0
            self.peer_contributions = 0

    def get_stats(self) -> dict:
        """Return stats for debug overlay."""
        with self._lock:
            return {
                "peer_count": len(self._peer_buffers),
                "delay_ms": int(self._merge_delay_sec * 1000),
                "merge_count": self.merge_count,
                "peer_contributions": self.peer_contributions,
                "buffered_local": len(self._local_queue),
            }

    # ── internals (caller holds lock) ─────────────────────────

    def _merge_with_peers(self, local_chunk: np.ndarray, local_ts: float) -> np.ndarray:
        """Energy-weighted merge of local chunk with time-aligned peer audio."""
        chunk_len = len(local_chunk)
        chunk_duration = chunk_len / SAMPLE_RATE

        # Collect sources: (chunk, rms)
        sources: list[tuple[np.ndarray, float]] = []

        # Local source
        local_rms = float(np.sqrt(np.mean(local_chunk ** 2)))
        sources.append((local_chunk, local_rms))

        # Peer sources — find the best-aligned chunk for each peer
        for node_id, peer_buf in self._peer_buffers.items():
            peer_chunk = self._get_aligned_peer_chunk(peer_buf, local_ts, chunk_len, chunk_duration)
            if peer_chunk is not None:
                peer_rms = float(np.sqrt(np.mean(peer_chunk ** 2)))
                sources.append((peer_chunk, peer_rms))

        self.merge_count += 1

        # Single source — no mixing needed
        if len(sources) == 1:
            return local_chunk

        self.peer_contributions += 1

        # Energy-weighted averaging
        weights = []
        for _, rms in sources:
            if rms < P2P_AUDIO_QUALITY_GATE:
                weights.append(0.0)
            else:
                weights.append(np.sqrt(rms))

        total_weight = sum(weights)
        if total_weight < 1e-10:
            # All sources below gate — return local as-is
            return local_chunk

        # Normalize weights
        weights = [w / total_weight for w in weights]

        # Weighted sum
        mixed = np.zeros(chunk_len, dtype=np.float32)
        for (chunk, _), weight in zip(sources, weights):
            if weight > 0:
                mixed += weight * chunk

        # Gentle boost + clip
        return np.clip(mixed * 1.2, -1.0, 1.0)

    def _get_aligned_peer_chunk(
        self,
        peer_buf: collections.deque,
        target_ts: float,
        chunk_len: int,
        chunk_duration: float,
    ) -> np.ndarray | None:
        """Find the peer chunk closest in time to the target timestamp."""
        if not peer_buf:
            return None

        best_chunk = None
        best_delta = float("inf")
        tolerance = chunk_duration * 2  # allow 2x chunk duration of misalignment

        for ts, chunk in peer_buf:
            delta = abs(ts - target_ts)
            if delta < best_delta:
                best_delta = delta
                best_chunk = chunk

        if best_delta > tolerance or best_chunk is None:
            return None

        # Ensure same length as local chunk
        if len(best_chunk) == chunk_len:
            return best_chunk
        elif len(best_chunk) > chunk_len:
            return best_chunk[:chunk_len]
        else:
            padded = np.zeros(chunk_len, dtype=np.float32)
            padded[: len(best_chunk)] = best_chunk
            return padded
