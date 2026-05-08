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
from network.segments import LOCAL_NODE_ID


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

        # Live weight tracking: node_id → rolling average weight (0.0–1.0)
        # LOCAL_NODE_ID key for local mic. Updated every merge.
        self._live_weights: dict[str, float] = {}
        self._weight_alpha = 0.15  # EMA smoothing factor

        # Dominant source: which mic has the highest weight right now
        self._dominant_source: str = LOCAL_NODE_ID

    @property
    def merge_delay_sec(self) -> float:
        return self._merge_delay_sec

    @property
    def dominant_source(self) -> str:
        """Node ID of the source with the highest current weight."""
        with self._lock:
            return self._dominant_source

    @property
    def active_peers(self) -> int:
        with self._lock:
            return len(self._peer_buffers)

    def feed_peer(self, node_id: str, pcm_float32: np.ndarray, local_timestamp: float) -> None:
        """Feed a chunk from a peer (timestamp already adjusted to local clock)."""
        with self._lock:
            if node_id not in self._peer_buffers:
                # ~2s worth at 50 chunks/sec
                self._peer_buffers[node_id] = collections.deque(maxlen=200)
            self._peer_buffers[node_id].append((local_timestamp, pcm_float32))

    def remove_peer(self, node_id: str) -> list[np.ndarray]:
        """Remove a peer's buffer when they disconnect.

        If this was the last peer, drain any local chunks still waiting in
        the delay buffer so they aren't lost — returns them as merged
        chunks (with whatever peer audio remained).
        """
        drained: list[np.ndarray] = []
        with self._lock:
            self._peer_buffers.pop(node_id, None)
            if not self._peer_buffers:
                while self._local_queue:
                    ts, local_chunk = self._local_queue.popleft()
                    drained.append(self._merge_with_peers(local_chunk, ts))
        return drained

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
            self._live_weights.clear()

    def get_stats(self) -> dict:
        """Return stats for debug overlay."""
        with self._lock:
            return {
                "peer_count": len(self._peer_buffers),
                "delay_ms": int(self._merge_delay_sec * 1000),
                "merge_count": self.merge_count,
                "peer_contributions": self.peer_contributions,
                "buffered_local": len(self._local_queue),
                "live_weights": dict(self._live_weights),
            }

    # ── internals (caller holds lock) ─────────────────────────

    def _merge_with_peers(self, local_chunk: np.ndarray, local_ts: float) -> np.ndarray:
        """Energy-weighted merge of local chunk with time-aligned peer audio."""
        chunk_len = len(local_chunk)
        chunk_duration = chunk_len / SAMPLE_RATE

        # Collect sources: (node_id, chunk, rms)
        sources: list[tuple[str, np.ndarray, float]] = []

        # Local source
        local_rms = float(np.sqrt(np.mean(local_chunk ** 2)))
        sources.append((LOCAL_NODE_ID, local_chunk, local_rms))

        # Peer sources — find the best-aligned chunk for each peer
        for node_id, peer_buf in self._peer_buffers.items():
            peer_chunk = self._get_aligned_peer_chunk(peer_buf, local_ts, chunk_len, chunk_duration)
            if peer_chunk is not None:
                peer_rms = float(np.sqrt(np.mean(peer_chunk ** 2)))
                sources.append((node_id, peer_chunk, peer_rms))

        self.merge_count += 1

        # Single source — no mixing needed
        if len(sources) == 1:
            self._update_live_weights([(LOCAL_NODE_ID, 1.0)])
            return local_chunk

        self.peer_contributions += 1

        # Energy-weighted averaging
        weights = []
        for _, _, rms in sources:
            if rms < P2P_AUDIO_QUALITY_GATE:
                weights.append(0.0)
            else:
                weights.append(np.sqrt(rms))

        total_weight = sum(weights)
        if total_weight < 1e-10:
            # All sources below gate — return local as-is
            self._update_live_weights([(nid, 0.0) for nid, _, _ in sources])
            return local_chunk

        # Normalize weights
        weights = [w / total_weight for w in weights]

        # Track live weights
        self._update_live_weights(
            [(nid, w) for (nid, _, _), w in zip(sources, weights)]
        )

        # Weighted sum
        mixed = np.zeros(chunk_len, dtype=np.float32)
        for (_, chunk, _), weight in zip(sources, weights):
            if weight > 0:
                mixed += weight * chunk

        # Gentle boost + clip
        return np.clip(mixed * 1.2, -1.0, 1.0)

    def _update_live_weights(self, source_weights: list[tuple[str, float]]) -> None:
        """Update EMA-smoothed live weights for each source."""
        a = self._weight_alpha
        seen = set()
        for nid, w in source_weights:
            seen.add(nid)
            prev = self._live_weights.get(nid, 0.0)
            self._live_weights[nid] = prev * (1 - a) + w * a
        # Decay sources not present in this merge
        for nid in list(self._live_weights):
            if nid not in seen:
                self._live_weights[nid] *= (1 - a)
                if self._live_weights[nid] < 0.001:
                    del self._live_weights[nid]
        # Track dominant source
        if self._live_weights:
            self._dominant_source = max(self._live_weights, key=self._live_weights.get)

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
