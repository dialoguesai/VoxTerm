"""Time-aligned mixing of two equal-rate mono float32 chunk streams (mic + system audio).

The single home for this operation — both the TUI (`tui/app.py`) and the GUI engine
(`gui/engine.py`) drive recording from a mic stream plus an optional system-audio stream.
(Distinct from `audio/merger.py`, which is the P2P energy-weighted multi-peer mixer.)
"""

from __future__ import annotations

import numpy as np


def mix_chunks(mic: list, sysaud: list) -> list:
    """Sum the overlapping chunks (clipped to [-1, 1]), then append each stream's tail.

    The streams arrive as lists of equal-length float32 chunks; we sum index-for-index
    for the first ``min(len)`` chunks and keep whichever stream has the longer tail, so
    no audio is dropped when one side is briefly ahead.
    """
    n = min(len(mic), len(sysaud))
    mixed = [np.clip(mic[i] + sysaud[i], -1.0, 1.0) for i in range(n)]
    mixed.extend(mic[n:])
    mixed.extend(sysaud[n:])
    return mixed
