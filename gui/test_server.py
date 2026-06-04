"""Tests for gui.server — non-mic routes only.

Starts gui.server's Handler in-process on an EPHEMERAL port (port 0) in a daemon
thread, talks to it with http.client, and tears it down. Covers ONLY routes that
never touch the microphone / transcriber:

    GET /                  -> text/html
    GET /static/app.js     -> text/javascript
    GET /static/../server.py (traversal) -> 403/404, never server.py's bytes
    GET /api/options       -> {models, languages}
    GET /api/status        -> idle shape
    GET /api/sessions      -> {sessions: [...]}
    GET /nope              -> 404
    LAN-auth contract on /api/options:
        TOKEN set, no token            -> 401
        TOKEN set, ?token=<correct>    -> 200
        TOKEN set, ?token=<wrong>      -> 401
        TOKEN = None (loopback)        -> open (200)

It NEVER POSTs to /api/record/* (those open the mic). To keep the test off the
user's real ~/voxterm-live and make /api/sessions deterministic, server.ENGINE is
swapped for a fresh Engine rooted at a temp dir for the duration of the tests.
"""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import gui.server as server
from gui.engine import Engine


# --------------------------------------------------------------------------- #
# server lifecycle helper
# --------------------------------------------------------------------------- #
@contextmanager
def running_server(token=None, engine=None):
    """Bring up server.Handler on an ephemeral port in a daemon thread.

    Sets server.TOKEN / server.ENGINE module globals for the duration, then
    restores them. Yields ("127.0.0.1", port).
    """
    old_token = server.TOKEN
    old_engine = server.ENGINE
    server.TOKEN = token
    if engine is not None:
        server.ENGINE = engine

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield "127.0.0.1", port
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)
        server.TOKEN = old_token
        server.ENGINE = old_engine


def _get(host, port, path, headers=None):
    """GET path; return (status, content_type, body_bytes)."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader("Content-Type"), body
    finally:
        conn.close()


def _json_get(host, port, path, headers=None):
    status, ctype, body = _get(host, port, path, headers=headers)
    return status, json.loads(body.decode("utf-8"))


@contextmanager
def temp_engine():
    """A fresh Engine rooted at a temp dir (keeps tests off ~/voxterm-live)."""
    with tempfile.TemporaryDirectory() as d:
        yield Engine(out_dir=Path(d))


# --------------------------------------------------------------------------- #
# static routes
# --------------------------------------------------------------------------- #
def test_root_serves_html():
    with running_server() as (h, p):
        status, ctype, body = _get(h, p, "/")
        assert status == 200, status
        assert ctype is not None and ctype.startswith("text/html"), ctype
        assert len(body) > 0


def test_index_html_alias_serves_html():
    with running_server() as (h, p):
        status, ctype, _ = _get(h, p, "/index.html")
        assert status == 200, status
        assert ctype is not None and ctype.startswith("text/html"), ctype


def test_static_appjs_serves_javascript():
    with running_server() as (h, p):
        status, ctype, body = _get(h, p, "/static/app.js")
        assert status == 200, status
        assert ctype is not None and ctype.startswith("text/javascript"), ctype
        assert len(body) > 0


def test_static_traversal_is_blocked():
    """/static/../server.py must NOT leak server.py's bytes; expect 403 or 404."""
    server_py = Path(server.__file__).resolve()
    secret = server_py.read_bytes()
    with running_server() as (h, p):
        status, ctype, body = _get(h, p, "/static/../server.py")
        assert status in (403, 404), status
        # the server source must never appear in the response body
        assert body != secret
        assert b"def do_GET" not in body
        assert b"ThreadingHTTPServer" not in body


# --------------------------------------------------------------------------- #
# api routes (idle / read-only)
# --------------------------------------------------------------------------- #
def test_api_options_returns_models_and_languages():
    with temp_engine() as eng, running_server(engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/options")
        assert status == 200, status
        assert "models" in data and "languages" in data, data
        assert isinstance(data["models"], list) and data["models"], data
        assert isinstance(data["languages"], dict) and data["languages"], data
        # languages map code -> display name (e.g. "en" -> "English")
        assert "en" in data["languages"], data["languages"]


def test_api_status_idle_shape():
    with temp_engine() as eng, running_server(engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/status")
        assert status == 200, status
        assert data["recording"] is False, data
        assert data["level"] == 0.0, data
        assert data["elapsed"] == 0, data
        assert data["job"] == {"state": "idle"}, data


def test_api_sessions_returns_list():
    with temp_engine() as eng, running_server(engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/sessions")
        assert status == 200, status
        assert "sessions" in data, data
        assert isinstance(data["sessions"], list), data


def test_unknown_route_404():
    with running_server() as (h, p):
        status, data = _json_get(h, p, "/nope/does/not/exist")
        assert status == 404, status
        assert data.get("error") == "not found", data


# --------------------------------------------------------------------------- #
# LAN-auth contract
# --------------------------------------------------------------------------- #
def test_auth_missing_token_401():
    with temp_engine() as eng, running_server(token="s3cret-tok", engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/options")
        assert status == 401, status
        assert data.get("error") == "unauthorized", data


def test_auth_correct_query_token_200():
    with temp_engine() as eng, running_server(token="s3cret-tok", engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/options?token=s3cret-tok")
        assert status == 200, status
        assert "models" in data, data


def test_auth_wrong_token_401():
    with temp_engine() as eng, running_server(token="s3cret-tok", engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/options?token=nope")
        assert status == 401, status
        assert data.get("error") == "unauthorized", data


def test_auth_correct_header_token_200():
    """The X-VoxTerm-Token header is also a valid credential."""
    with temp_engine() as eng, running_server(token="s3cret-tok", engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/options",
                                 headers={"X-VoxTerm-Token": "s3cret-tok"})
        assert status == 200, status
        assert "models" in data, data


def test_auth_none_is_open():
    """TOKEN = None (loopback default) means /api/* needs no token."""
    with temp_engine() as eng, running_server(token=None, engine=eng) as (h, p):
        status, data = _json_get(h, p, "/api/options")
        assert status == 200, status
        assert "models" in data, data


def test_static_open_even_when_token_set():
    """Token gate applies to /api/* only; static assets stay reachable."""
    with running_server(token="s3cret-tok") as (h, p):
        status, ctype, _ = _get(h, p, "/")
        assert status == 200, status
        assert ctype is not None and ctype.startswith("text/html"), ctype


# --------------------------------------------------------------------------- #
# standalone runner (no pytest needed)
# --------------------------------------------------------------------------- #
def _run_all():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
