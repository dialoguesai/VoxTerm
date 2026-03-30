"""Transcript assembly — merges segments from multiple peers.

Each node independently assembles its own transcript.  No consensus,
no reconciliation.  Different nodes may produce different text.

The TranscriptAssembler maintains the merged transcript as an ordered
list of finalized segments, plus a set of in-progress partials (one
per peer at most).
"""

from __future__ import annotations

import bisect
import threading
from dataclasses import dataclass, field

from network.clock import ClockSync

# Sentinel node_id for locally-generated segments.
LOCAL_NODE_ID = "__local__"


@dataclass
class MergedSegment:
    """A transcript segment from any peer, with clock-adjusted timestamps."""

    node_id: str
    seq: int
    speaker_name: str
    text: str
    start_ts: float          # original remote timestamp
    end_ts: float             # original remote timestamp
    confidence: float
    is_partial: bool
    adjusted_start_ts: float = 0.0   # converted to local clock
    adjusted_end_ts: float = 0.0     # converted to local clock
    dominant_mic: str = ""           # node_id of dominant audio source (local segments only)

    def __post_init__(self):
        if self.adjusted_start_ts == 0.0:
            self.adjusted_start_ts = self.start_ts
        if self.adjusted_end_ts == 0.0:
            self.adjusted_end_ts = self.end_ts


class TranscriptAssembler:
    """Merges finalized transcript segments from all peers into one timeline."""

    # Keep at most this many finals to prevent unbounded memory growth.
    MAX_FINALS = 5000

    def __init__(self):
        self._finals: list[MergedSegment] = []
        self._partials: dict[str, MergedSegment] = {}  # node_id → latest partial
        self._seen: set[tuple[str, int]] = set()  # (node_id, seq) dedup
        self._lock = threading.Lock()

    def on_final(
        self,
        node_id: str,
        seq: int,
        speaker_name: str,
        text: str,
        start_ts: float,
        end_ts: float,
        confidence: float,
        clock_sync: ClockSync | None = None,
    ) -> MergedSegment | None:
        """Insert a finalized segment, sorted by adjusted start time.

        Returns None if this (node_id, seq) was already received (dedup).
        """
        adjusted = clock_sync.adjust(start_ts) if clock_sync else start_ts
        adjusted_end = clock_sync.adjust(end_ts) if clock_sync else end_ts

        seg = MergedSegment(
            node_id=node_id,
            seq=seq,
            speaker_name=speaker_name,
            text=text,
            start_ts=start_ts,
            end_ts=end_ts,
            confidence=confidence,
            is_partial=False,
            adjusted_start_ts=adjusted,
            adjusted_end_ts=adjusted_end,
        )

        with self._lock:
            # Dedup: skip if we already received this (node_id, seq)
            key = (node_id, seq)
            if key in self._seen:
                return None
            self._seen.add(key)

            # Binary insert by adjusted_start_ts
            keys = [s.adjusted_start_ts for s in self._finals]
            idx = bisect.bisect_right(keys, adjusted)
            self._finals.insert(idx, seg)

            # Evict oldest segments if over capacity
            if len(self._finals) > self.MAX_FINALS:
                evicted = self._finals[:len(self._finals) - self.MAX_FINALS]
                self._finals = self._finals[len(self._finals) - self.MAX_FINALS:]
                # Also remove evicted entries from the dedup set
                for s in evicted:
                    self._seen.discard((s.node_id, s.seq))

            # Clear any pending partial for this node with same seq
            if node_id in self._partials and self._partials[node_id].seq == seq:
                del self._partials[node_id]

        return seg

    def add_local(
        self,
        seq: int,
        speaker_name: str,
        text: str,
        start_ts: float,
        end_ts: float,
        confidence: float = 0.9,
        dominant_mic: str = "",
    ) -> MergedSegment:
        """Insert a locally-generated segment into the merged timeline."""
        seg = MergedSegment(
            node_id=LOCAL_NODE_ID,
            seq=seq,
            speaker_name=speaker_name,
            text=text,
            start_ts=start_ts,
            end_ts=end_ts,
            confidence=confidence,
            is_partial=False,
            adjusted_start_ts=start_ts,
            adjusted_end_ts=end_ts,
            dominant_mic=dominant_mic,
        )
        with self._lock:
            key = (LOCAL_NODE_ID, seq)
            if key in self._seen:
                return seg
            self._seen.add(key)
            keys = [s.adjusted_start_ts for s in self._finals]
            idx = bisect.bisect_right(keys, start_ts)
            self._finals.insert(idx, seg)
            if len(self._finals) > self.MAX_FINALS:
                evicted = self._finals[:len(self._finals) - self.MAX_FINALS]
                self._finals = self._finals[len(self._finals) - self.MAX_FINALS:]
                for s in evicted:
                    self._seen.discard((s.node_id, s.seq))
        return seg

    def on_partial(
        self,
        node_id: str,
        seq: int,
        speaker_name: str,
        text: str,
        start_ts: float,
        clock_sync: ClockSync | None = None,
    ) -> MergedSegment:
        """Store/replace the in-progress partial for a peer."""
        adjusted = clock_sync.adjust(start_ts) if clock_sync else start_ts

        seg = MergedSegment(
            node_id=node_id,
            seq=seq,
            speaker_name=speaker_name,
            text=text,
            start_ts=start_ts,
            end_ts=start_ts,  # no end yet
            confidence=0.0,
            is_partial=True,
            adjusted_start_ts=adjusted,
        )
        with self._lock:
            self._partials[node_id] = seg
        return seg

    def get_finals(self) -> list[MergedSegment]:
        """All finalized segments, ordered by adjusted start time."""
        with self._lock:
            return list(self._finals)

    def get_partials(self) -> list[MergedSegment]:
        """Current in-progress partials from all peers."""
        with self._lock:
            return list(self._partials.values())

    def clear(self) -> None:
        """Reset all state — call when user clears the transcript."""
        with self._lock:
            self._finals.clear()
            self._partials.clear()
            self._seen.clear()

    def clear_peer(self, node_id: str) -> None:
        """Remove pending partials for a disconnected peer."""
        with self._lock:
            self._partials.pop(node_id, None)

    @property
    def final_count(self) -> int:
        return len(self._finals)

    @property
    def partial_count(self) -> int:
        return len(self._partials)
