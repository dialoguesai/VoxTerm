"""Tests for tui/remote_upload.py — multipart encoding + end-to-end POST."""

import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tui.remote_upload import _encode_multipart, build_metadata, upload_session


# ── Multipart encoder ────────────────────────────────────────────


class TestMultipartEncoder:

    def test_text_part(self):
        body, ctype = _encode_multipart([
            ("greeting", "hello", None, None),
        ])
        assert ctype.startswith("multipart/form-data; boundary=")
        assert b'name="greeting"' in body
        assert b"hello" in body

    def test_file_part(self):
        body, _ = _encode_multipart([
            ("audio", b"\x00\x01\x02", "x.wav", "audio/wav"),
        ])
        assert b'filename="x.wav"' in body
        assert b"Content-Type: audio/wav" in body
        assert b"\x00\x01\x02" in body

    def test_mixed_parts(self):
        body, _ = _encode_multipart([
            ("metadata", '{"k":"v"}', "metadata.json", "application/json"),
            ("transcript", b"# hi", "s.md", "text/markdown"),
            ("audio", b"WAVE", "s.wav", "audio/wav"),
        ])
        # All three field names present
        for name in (b'name="metadata"', b'name="transcript"', b'name="audio"'):
            assert name in body
        # Boundary closing token at end
        assert body.rstrip(b"\r\n").endswith(b"--")


# ── End-to-end POST against a local server ───────────────────────


class _Echo(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _Echo.received = {
            "content_type": self.headers.get("Content-Type", ""),
            "body_len": len(body),
            "body": body,
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args, **kwargs):
        pass


@pytest.fixture
def echo_server():
    _Echo.received = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/v1/transcripts"
    finally:
        server.shutdown()
        server.server_close()


def _write_wav(path: Path, n_samples: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * n_samples)


class TestUploadSession:

    def test_uploads_transcript_only(self, echo_server, tmp_path):
        md = tmp_path / "s.md"
        md.write_text("# hello", encoding="utf-8")
        meta = build_metadata(
            session_id="2026-05-03_120000",
            model_name="qwen3-0.6b",
            language="en",
            started_at="2026-05-03T12:00:00",
            ended_at="2026-05-03T12:01:00",
            entry_count=3,
            voxterm_version="0.1.0",
        )
        result = upload_session(echo_server, "2026-05-03_120000", md, None, meta)
        assert result.ok, result.message
        assert result.status == 200
        assert _Echo.received["content_type"].startswith("multipart/form-data")
        body = _Echo.received["body"]
        assert b"# hello" in body
        assert b'"session_id": "2026-05-03_120000"' in body
        assert b'"hostname"' in body  # added by build_metadata
        assert b'name="audio"' not in body  # no audio uploaded

    def test_uploads_transcript_plus_audio(self, echo_server, tmp_path):
        md = tmp_path / "s.md"
        md.write_text("# audio test", encoding="utf-8")
        wav = tmp_path / "s.wav"
        _write_wav(wav, n_samples=8000)
        meta = build_metadata(
            session_id="sid",
            model_name="m",
            language="en",
            started_at="x",
            ended_at="y",
            entry_count=1,
            voxterm_version="0.1.0",
        )
        result = upload_session(echo_server, "sid", md, wav, meta)
        assert result.ok, result.message
        body = _Echo.received["body"]
        assert b"# audio test" in body
        assert b'name="audio"' in body
        assert b'filename="sid.wav"' in body
        # WAV begins with RIFF header
        assert b"RIFF" in body

    def test_network_error_returns_failure_result(self, tmp_path):
        md = tmp_path / "s.md"
        md.write_text("x", encoding="utf-8")
        meta = build_metadata(
            session_id="sid", model_name="m", language="en",
            started_at="x", ended_at="y", entry_count=0, voxterm_version="0",
        )
        # Port 1 is reserved; connection should fail fast.
        result = upload_session(
            "http://127.0.0.1:1/upload", "sid", md, None, meta,
            connect_timeout=1.0, read_timeout=1.0,
        )
        assert not result.ok
        assert result.message
