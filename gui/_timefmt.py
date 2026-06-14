"""The single hh:mm:ss formatter for the GUI — shared by the live transcriber, the
exporter, and the engine so live and exported timestamps round identically (no ±1s drift)."""

from __future__ import annotations


def fmt_hms(seconds: float) -> str:
    """Seconds → ``M:SS`` (or ``H:MM:SS`` past an hour), rounded to the nearest second."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
