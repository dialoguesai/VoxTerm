import threading
import numpy as np
from config import SAMPLE_RATE


class AudioBuffer:
    """Thread-safe audio accumulator for transcription chunks."""

    def __init__(self):
        self._buffer: list[np.ndarray] = []
        self._total_samples = 0
        self._lock = threading.Lock()

    def append(self, chunk: np.ndarray):
        with self._lock:
            self._buffer.append(chunk)
            self._total_samples += len(chunk)

    def get_and_clear(self) -> np.ndarray:
        with self._lock:
            if not self._buffer:
                return np.array([], dtype=np.float32)
            audio = np.concatenate(self._buffer)
            self._buffer.clear()
            self._total_samples = 0
            return audio

    @property
    def duration(self) -> float:
        with self._lock:
            return self._total_samples / SAMPLE_RATE

    def get_audio(self) -> np.ndarray:
        """Return concatenated audio WITHOUT clearing the buffer."""
        with self._lock:
            if not self._buffer:
                return np.array([], dtype=np.float32)
            return np.concatenate(self._buffer)

    def trim_front(self, seconds: float):
        """Remove audio from the front of the buffer up to `seconds`.

        Used by the overlapping-chunk pipeline to slide the transcription
        window forward after committing words, keeping uncommitted audio
        for the next tick.
        """
        if seconds <= 0:
            return
        with self._lock:
            if not self._buffer:
                return
            trim_samples = int(seconds * SAMPLE_RATE)
            if trim_samples <= 0:
                return
            full = np.concatenate(self._buffer)
            trimmed = full[trim_samples:]
            self._buffer.clear()
            if len(trimmed) > 0:
                self._buffer.append(trimmed)
                self._total_samples = len(trimmed)
            else:
                self._total_samples = 0

    def clear(self):
        with self._lock:
            self._buffer.clear()
            self._total_samples = 0
