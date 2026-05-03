"""Continuous WAV recording of the audio that gets transcribed during a session.

Streams to a temp file on disk so RAM use stays bounded for long sessions.
The temp file is finalized + handed to the upload pipeline on save, or
deleted on discard / new-session.
"""

from __future__ import annotations

import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from config import SAMPLE_RATE


class SessionAudioRecorder:
    """Buffer audio chunks during a recording session into a temp WAV file.

    Float32 [-1, 1] in -> int16 PCM mono 16kHz WAV out.
    Thread-safe: append() is called from the audio loop;
    finalize/discard from action handlers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wav: Optional[wave.Wave_write] = None
        self._path: Optional[Path] = None
        self._session_id: Optional[str] = None

    def start_if_needed(self, session_id: str) -> None:
        with self._lock:
            if self._wav is not None and self._session_id == session_id:
                return
            if self._wav is not None:
                # Different session — discard the prior file
                self._close_locked(delete=True)
            tmp = tempfile.NamedTemporaryFile(
                prefix=f"voxterm-{session_id}-",
                suffix=".wav",
                delete=False,
            )
            tmp.close()
            path = Path(tmp.name)
            wav = wave.open(str(path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            self._wav = wav
            self._path = path
            self._session_id = session_id

    def append(self, chunk: np.ndarray) -> None:
        if self._wav is None:
            return
        with self._lock:
            if self._wav is None:
                return
            pcm = np.clip(chunk * 32767.0, -32768, 32767).astype(np.int16)
            try:
                self._wav.writeframes(pcm.tobytes())
            except Exception:
                pass  # never break the audio loop on I/O error

    def finalize(self) -> Optional[Path]:
        """Close the WAV and return its path. Caller takes ownership of the file."""
        with self._lock:
            if self._wav is None:
                return None
            try:
                self._wav.close()
            except Exception:
                pass
            path = self._path
            self._wav = None
            self._path = None
            self._session_id = None
            return path

    def discard(self) -> None:
        with self._lock:
            self._close_locked(delete=True)

    def _close_locked(self, *, delete: bool) -> None:
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        if delete and self._path is not None and self._path.exists():
            try:
                self._path.unlink()
            except Exception:
                pass
        self._path = None
        self._session_id = None

    @property
    def duration_seconds(self) -> float:
        with self._lock:
            if self._wav is None:
                return 0.0
            try:
                return self._wav.tell() / SAMPLE_RATE
            except Exception:
                return 0.0
