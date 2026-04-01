"""Tests for the binary IPC protocol in diarization/ipc.py."""

import os
import threading

import numpy as np
import pytest

from diarization.ipc import encode_array, decode_array, send_msg, recv_msg


class TestEncodeDecodeArray:

    def test_encode_decode_array(self):
        rng = np.random.RandomState(42)
        arr = rng.randn(512).astype(np.float32)
        encoded = encode_array(arr)
        decoded = decode_array(encoded)
        assert decoded.dtype == np.float32
        assert np.allclose(arr, decoded)

    def test_encode_empty_array(self):
        arr = np.array([], dtype=np.float32)
        encoded = encode_array(arr)
        decoded = decode_array(encoded)
        assert decoded.dtype == np.float32
        assert len(decoded) == 0
        assert np.allclose(arr, decoded)


class TestSendRecvMessage:

    def _roundtrip(self, msg):
        """Send a message through a real OS pipe and receive it back.

        Uses a writer thread to avoid deadlocking on large payloads that
        exceed the OS pipe buffer size.
        """
        r_fd, w_fd = os.pipe()
        r_pipe = os.fdopen(r_fd, "rb")
        w_pipe = os.fdopen(w_fd, "wb")

        def _writer():
            try:
                send_msg(w_pipe, msg)
            finally:
                w_pipe.close()

        t = threading.Thread(target=_writer)
        t.start()
        try:
            result = recv_msg(r_pipe)
        finally:
            r_pipe.close()
            t.join(timeout=5)
        return result

    def test_send_recv_message(self):
        msg = {"type": "identify", "speaker_id": 3, "label": "Alice"}
        received = self._roundtrip(msg)
        assert received == msg

    def test_send_recv_large_message(self):
        """15 seconds of 16kHz audio encoded as a hex payload."""
        audio = np.random.randn(16000 * 15).astype(np.float32)
        msg = {"type": "identify", "audio": encode_array(audio)}
        received = self._roundtrip(msg)
        assert received is not None
        recovered = decode_array(received["audio"])
        assert np.allclose(audio, recovered)

    def test_recv_eof(self):
        r_fd, w_fd = os.pipe()
        r_pipe = os.fdopen(r_fd, "rb")
        os.close(w_fd)  # immediate EOF
        try:
            result = recv_msg(r_pipe)
            assert result is None
        finally:
            r_pipe.close()

    def test_recv_truncated(self):
        """A partial header (fewer than 4 bytes) should return None."""
        r_fd, w_fd = os.pipe()
        r_pipe = os.fdopen(r_fd, "rb")
        os.write(w_fd, b"\x05\x00")  # only 2 bytes of a 4-byte header
        os.close(w_fd)
        try:
            result = recv_msg(r_pipe)
            assert result is None
        finally:
            r_pipe.close()
