"""End-to-end tests for the VoxTerm collector."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXTERM_SERVER_DATA_DIR", str(tmp_path))
    # Reload the module so it picks up the new data dir.
    import importlib
    import server.app
    importlib.reload(server.app)
    return TestClient(server.app.app), tmp_path


def _meta(session_id="2026-05-03_120000", **overrides):
    base = {
        "session_id": session_id,
        "hostname": "test-host",
        "model_name": "qwen3-0.6b",
        "language": "en",
        "started_at": "2026-05-03T12:00:00",
        "ended_at": "2026-05-03T12:01:00",
        "entry_count": 3,
        "voxterm_version": "0.1.0",
    }
    base.update(overrides)
    return json.dumps(base)


# ── /healthz ──────────────────────────────────────────────────────


def test_healthz(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── POST /v1/transcripts ──────────────────────────────────────────


class TestUpload:

    def test_transcript_only(self, client):
        c, root = client
        r = c.post(
            "/v1/transcripts",
            data={"metadata": _meta()},
            files={"transcript": ("s.md", b"# hello", "text/markdown")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["session_id"] == "2026-05-03_120000"
        assert body["audio_bytes"] is None

        sd = root / "uploads" / "2026-05-03_120000"
        assert (sd / "transcript.md").read_bytes() == b"# hello"
        assert (sd / "metadata.json").exists()
        assert not (sd / "audio.wav").exists()

    def test_with_audio(self, client):
        c, root = client
        wav = b"RIFF" + b"\x00" * 100  # not a real WAV; bytes are opaque to server
        r = c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-audio")},
            files={
                "transcript": ("s.md", b"hi", "text/markdown"),
                "audio": ("s.wav", wav, "audio/wav"),
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["audio_bytes"] == len(wav)
        sd = root / "uploads" / "sid-audio"
        assert (sd / "audio.wav").read_bytes() == wav

    def test_idempotent_on_session_id(self, client):
        c, root = client
        for body in (b"first", b"second"):
            r = c.post(
                "/v1/transcripts",
                data={"metadata": _meta(session_id="sid-replay")},
                files={"transcript": ("s.md", body, "text/markdown")},
            )
            assert r.status_code == 200
        # Latest write wins
        assert (root / "uploads" / "sid-replay" / "transcript.md").read_bytes() == b"second"

    def test_invalid_metadata_json(self, client):
        c, _ = client
        r = c.post(
            "/v1/transcripts",
            data={"metadata": "not-json"},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        assert r.status_code == 400

    def test_missing_session_id(self, client):
        c, _ = client
        r = c.post(
            "/v1/transcripts",
            data={"metadata": json.dumps({"hostname": "h"})},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        assert r.status_code == 400

    @pytest.mark.parametrize(
        "bad_id",
        ["..", "../escape", "a/b", "x\\y", ".", "", " ", "has space", "x\x00y"],
    )
    def test_rejects_bad_session_id(self, client, bad_id):
        c, _ = client
        r = c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id=bad_id)},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        assert r.status_code == 400

    def test_rejects_non_object_metadata(self, client):
        c, _ = client
        r = c.post(
            "/v1/transcripts",
            data={"metadata": "[]"},  # valid JSON, wrong shape
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        assert r.status_code == 400

    def test_rejects_non_integer_entry_count(self, client):
        c, _ = client
        meta = json.loads(_meta())
        meta["entry_count"] = "not-a-number"
        r = c.post(
            "/v1/transcripts",
            data={"metadata": json.dumps(meta)},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        # Bad client input → 400, not 500
        assert r.status_code == 400

    def test_reupload_without_audio_clears_stale(self, client):
        c, root = client
        # First upload includes audio
        c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-clear")},
            files={
                "transcript": ("s.md", b"v1", "text/markdown"),
                "audio": ("s.wav", b"RIFF" + b"\x00" * 50, "audio/wav"),
            },
        )
        wav = root / "uploads" / "sid-clear" / "audio.wav"
        assert wav.exists()
        # Re-upload without audio
        c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-clear")},
            files={"transcript": ("s.md", b"v2", "text/markdown")},
        )
        assert not wav.exists()
        record = c.get("/v1/transcripts/sid-clear").json()
        assert record["has_audio"] is False


# ── GET /v1/transcripts ──────────────────────────────────────────


class TestList:

    def test_list_returns_uploaded(self, client):
        c, _ = client
        for sid in ("a", "b", "c"):
            c.post(
                "/v1/transcripts",
                data={"metadata": _meta(session_id=sid)},
                files={"transcript": ("s.md", b"x", "text/markdown")},
            )
        r = c.get("/v1/transcripts")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 3
        assert {it["session_id"] for it in items} == {"a", "b", "c"}

    def test_list_pagination_bounds(self, client):
        c, _ = client
        assert c.get("/v1/transcripts?limit=0").status_code == 400
        assert c.get("/v1/transcripts?limit=1000").status_code == 400
        assert c.get("/v1/transcripts?offset=-1").status_code == 400


class TestFetch:

    def test_get_one(self, client):
        c, _ = client
        c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-fetch", entry_count=42)},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        r = c.get("/v1/transcripts/sid-fetch")
        assert r.status_code == 200
        body = r.json()
        assert body["entry_count"] == 42
        # Internal storage paths must not leak in API responses
        assert "transcript_path" not in body
        assert "audio_path" not in body
        assert body["has_audio"] is False

    def test_list_strips_paths(self, client):
        c, _ = client
        c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-list")},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        items = c.get("/v1/transcripts").json()["items"]
        assert items
        for item in items:
            assert "transcript_path" not in item
            assert "audio_path" not in item
            assert "has_audio" in item

    def test_get_one_404(self, client):
        c, _ = client
        assert c.get("/v1/transcripts/does-not-exist").status_code == 404

    def test_get_audio_streams_wav(self, client):
        c, _ = client
        wav = b"RIFFwavfake"
        c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-aud")},
            files={
                "transcript": ("s.md", b"x", "text/markdown"),
                "audio": ("s.wav", wav, "audio/wav"),
            },
        )
        r = c.get("/v1/transcripts/sid-aud/audio")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/wav")
        assert r.content == wav

    def test_get_audio_404_when_none_uploaded(self, client):
        c, _ = client
        c.post(
            "/v1/transcripts",
            data={"metadata": _meta(session_id="sid-noaud")},
            files={"transcript": ("s.md", b"x", "text/markdown")},
        )
        assert c.get("/v1/transcripts/sid-noaud/audio").status_code == 404


# ── Loopback safeguard ───────────────────────────────────────────


class TestLoopbackSafeguard:

    def test_loopback_addresses_pass(self, monkeypatch):
        monkeypatch.delenv("VOXTERM_ALLOW_PUBLIC", raising=False)
        from server.app import _refuse_if_public
        for host in ("127.0.0.1", "::1", "localhost"):
            _refuse_if_public(host)  # should not raise

    def test_public_address_refused(self, monkeypatch):
        monkeypatch.delenv("VOXTERM_ALLOW_PUBLIC", raising=False)
        from server.app import _refuse_if_public
        with pytest.raises(SystemExit):
            _refuse_if_public("0.0.0.0")

    def test_public_address_allowed_with_env(self, monkeypatch):
        monkeypatch.setenv("VOXTERM_ALLOW_PUBLIC", "1")
        from server.app import _refuse_if_public
        _refuse_if_public("0.0.0.0")  # should not raise
