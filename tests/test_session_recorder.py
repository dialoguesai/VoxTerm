"""Tests for audio/session_recorder.py."""

import wave

import numpy as np
import pytest

from audio.session_recorder import SessionAudioRecorder


class TestLifecycle:

    def test_start_then_finalize_produces_valid_wav(self):
        rec = SessionAudioRecorder()
        rec.start_if_needed("sess-1")
        rec.append(np.zeros(16000, dtype=np.float32))  # 1s of silence
        rec.append(np.full(8000, 0.5, dtype=np.float32))  # 0.5s tone
        path = rec.finalize()

        assert path is not None and path.exists()
        with wave.open(str(path), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == 16000
            assert w.getnframes() == 24000  # 1.5s at 16kHz
        path.unlink()

    def test_discard_deletes_file(self):
        rec = SessionAudioRecorder()
        rec.start_if_needed("sess-2")
        rec.append(np.zeros(1000, dtype=np.float32))
        # capture the path before discard wipes it
        path = rec._path
        rec.discard()
        assert path is not None
        assert not path.exists()

    def test_append_before_start_is_noop(self):
        rec = SessionAudioRecorder()
        # Should not raise
        rec.append(np.zeros(100, dtype=np.float32))
        assert rec.finalize() is None

    def test_start_if_needed_idempotent_within_session(self):
        rec = SessionAudioRecorder()
        rec.start_if_needed("sess-3")
        first_path = rec._path
        rec.start_if_needed("sess-3")
        assert rec._path == first_path
        rec.discard()

    def test_start_if_needed_replaces_on_new_session(self):
        rec = SessionAudioRecorder()
        rec.start_if_needed("sess-A")
        path_a = rec._path
        rec.start_if_needed("sess-B")
        path_b = rec._path
        assert path_a != path_b
        # Old session file should have been deleted
        assert not path_a.exists()
        rec.discard()

    def test_float_clipping(self):
        """Out-of-range floats should clip to int16 bounds, not wrap."""
        rec = SessionAudioRecorder()
        rec.start_if_needed("sess-clip")
        # Value > 1.0: should clip to int16 max
        rec.append(np.full(100, 5.0, dtype=np.float32))
        path = rec.finalize()
        with wave.open(str(path), "rb") as w:
            data = np.frombuffer(w.readframes(100), dtype=np.int16)
        assert np.all(data == 32767)
        path.unlink()
