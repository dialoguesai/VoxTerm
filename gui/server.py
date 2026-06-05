"""VoxTerm GUI control server — stdlib http.server, no extra deps.

Serves the web UI (gui/static/) + a small JSON API + an SSE status stream, all
backed by gui.engine (which reuses VoxTerm's own engine). Loopback-only by default;
set VOXTERM_GUI_LAN=1 to expose on the LAN (e.g. to drive it from your phone).

    python -m gui.server            # http://127.0.0.1:8740
    VOXTERM_GUI_LAN=1 python -m gui.server
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gui.engine import Engine  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8740
MAX_BODY = 64 * 1024            # API requests are tiny; bound them
MAX_SSE = 8                     # cap concurrent status streams
_sse_lock = threading.Lock()
_sse_count = 0

# When LAN-exposed (VOXTERM_GUI_LAN=1) every /api/* call must carry this token —
# without it, anyone on the wifi could start a recording of the room or read past
# transcripts. None = loopback (no token required). Set in main().
TOKEN = None

# Host-header allowlist (loopback mode) to block DNS-rebinding: a malicious site can't
# point its DNS at 127.0.0.1 and drive the tokenless local API, because the browser still
# sends Host: evil.com. None = no host check (LAN mode, which is token-gated instead). Set in main().
ALLOWED_HOSTS = None

ENGINE = Engine()

# Strict CSP: same-origin only, no external anything (the UI is fully self-hosted).
# style-src allows 'unsafe-inline' because the UI sets element.style (the live level
# ring, the progress bar) and per-speaker color dots; all interpolated values are
# escaped (app.js escapeHtml) and the data is local, so the exposure is minimal.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; media-src 'self'; connect-src 'self'; font-src 'self'; manifest-src 'self'; "
       "worker-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
           ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
           ".png": "image/png", ".webmanifest": "application/manifest+json", ".wav": "audio/wav"}


class Handler(BaseHTTPRequestHandler):
    server_version = "voxterm-gui"

    def _hdr(self, code=200, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._hdr(code, "application/json", {"Content-Length": str(len(body))})
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        # Without this, BaseHTTPRequestHandler answers HEAD with a default 501 that bypasses
        # the host/auth/security-header pipeline. Reject cleanly through _hdr instead.
        return self._json({"error": "method not allowed"}, 405)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (ValueError, TypeError):     # a malformed Content-Length must not crash the handler
            return {}
        if n <= 0 or n > MAX_BODY:
            if n > MAX_BODY:
                self.close_connection = True   # don't leave an undrained oversized body on the socket
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) or {}
        except Exception:
            return {}

    def log_message(self, *a):  # quiet unless VOXTERM_GUI_LOG=1 (request log for tests/debug)
        if os.environ.get("VOXTERM_GUI_LOG") == "1":
            super().log_message(*a)

    def _authed(self, q) -> bool:
        """Token check for /api/* when LAN-exposed. Loopback (TOKEN is None) is open."""
        if TOKEN is None:
            return True
        given = (self.headers.get("X-VoxTerm-Token")
                 or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                 or (q.get("token") or [""])[0])
        try:  # compare on bytes so a non-ASCII token yields a clean False (401), not a TypeError
            return bool(given) and secrets.compare_digest(given.encode("utf-8"), TOKEN.encode("utf-8"))
        except Exception:
            return False

    def _host_ok(self) -> bool:
        """Reject DNS-rebinding: in loopback mode the Host header must be a known local name.
        LAN mode skips this (the token is the gate; the LAN IP/hostname varies)."""
        if ALLOWED_HOSTS is None:
            return True
        return (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS

    def _same_origin(self) -> bool:
        """Block cross-origin state-changing requests (CSRF). Modern browsers send
        Sec-Fetch-Site (our own fetch() is 'same-origin'); when an Origin is present it must
        match Host. Non-browser clients (curl) send neither and are allowed for local tooling."""
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs is not None and sfs not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            netloc = urlparse(origin).netloc.lower()
            if netloc != (self.headers.get("Host") or "").lower():
                return False
        return True

    # ---- GET ----
    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p.startswith("/api/") and not self._same_origin():
            return self._json({"error": "cross-origin"}, 403)   # CSRF/read-leak guard on reads too
        if p.startswith("/api/") and not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        if p == "/" or p == "/index.html":
            return self._serve_static("index.html")
        if p == "/sw.js":                          # served at root so its SW scope is "/"
            return self._serve_static("sw.js")
        if p == "/manifest.webmanifest":
            return self._serve_static("manifest.webmanifest")
        if p.startswith("/static/"):
            return self._serve_static(p[len("/static/"):])
        if p == "/api/options":
            return self._json({"models": ENGINE.models(), "languages": ENGINE.languages(),
                               "default_model": ENGINE.default_model(),
                               "input_devices": ENGINE.input_devices()})
        if p == "/api/status":
            return self._json(ENGINE.status())
        if p == "/api/sessions":
            return self._json({"sessions": ENGINE.sessions()})
        if p == "/api/session":
            stem = (q.get("stem") or [""])[0]
            kind = (q.get("kind") or ["transcript"])[0]
            d = (q.get("dir") or [None])[0]
            return self._json(ENGINE.read_artifact(stem, kind, dir=d))
        if p == "/api/audio":
            return self._serve_audio(q)
        if p == "/api/events":
            return self._sse()
        return self._json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if not self._same_origin():
            return self._json({"error": "cross-origin"}, 403)
        if p.startswith("/api/") and not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        if p == "/api/record/start":
            b = self._read_json()
            return self._json(ENGINE.start_recording(device=b.get("device"), source=b.get("source", "mic")))
        if p == "/api/record/stop":
            b = self._read_json()
            return self._json(ENGINE.stop_recording(model=b.get("model") or None,
                                                     language=b.get("language", "en"),
                                                     diarize=b.get("diarize", True) is not False))
        if p == "/api/transcribe":
            b = self._read_json()
            return self._json(ENGINE.transcribe_existing(b.get("wav", ""), model=b.get("model") or None,
                                                         language=b.get("language", "en"),
                                                         diarize=b.get("diarize", True) is not False))
        if p == "/api/live/start":
            b = self._read_json()
            return self._json(ENGINE.live_start(b.get("wav")))
        if p == "/api/live/stop":
            return self._json(ENGINE.live_stop())
        if p == "/api/session/delete":
            b = self._read_json()
            return self._json(ENGINE.delete_session(b.get("stem", ""), dir=b.get("dir")))
        if p == "/api/export":
            b = self._read_json()
            return self._json(ENGINE.export_session(b.get("stem", ""), b.get("kind", "md"),
                                                    renames=b.get("renames") or {}, dir=b.get("dir")))
        if p == "/api/summarize":
            b = self._read_json()
            return self._json(ENGINE.summarize_session(
                b.get("stem", ""), dir=b.get("dir"), template_id=b.get("template", "tldr"),
                model=b.get("model", ""), custom_prompt=b.get("custom_prompt", "")))
        return self._json({"error": "not found"}, 404)

    def _serve_static(self, rel: str):
        # resolve within STATIC only (no traversal)
        target = (STATIC / rel).resolve()
        try:
            target.relative_to(STATIC.resolve())
        except ValueError:
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = _CTYPES.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self._hdr(200, ctype, {"Content-Length": str(len(data))})
        self.wfile.write(data)

    def _serve_audio(self, q):
        """Stream a session's source WAV (Download/playback). Honors a Range header so the
        <audio> element can seek (206 partial) and probe existence cheaply (bytes=0-0).
        Inherits the same-origin + token + host guards from do_GET (it's an /api route)."""
        stem = (q.get("stem") or [""])[0]
        d = (q.get("dir") or [None])[0]
        p = ENGINE.audio_path(stem, dir=d)
        if not p or not p.is_file():
            return self._json({"error": "not found"}, 404)
        try:
            size = p.stat().st_size
        except OSError:
            return self._json({"error": "not found"}, 404)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[len("bytes="):].partition("-")
                if s.strip():
                    start = int(s)
                    end = int(e) if e.strip() else size - 1
                elif e.strip():                 # suffix range bytes=-N (last N bytes)
                    start = max(0, size - int(e))
                if start > end or start >= size:
                    self._hdr(416, "audio/wav", {"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
                    return
                end = min(end, size - 1)
                status = 206
            except (ValueError, TypeError):
                start, end, status = 0, size - 1, 200
        length = end - start + 1
        # Bare ASCII filename for Content-Disposition (stem is a known session id; strip anyway)
        safe = "".join(c for c in stem if c.isalnum() or c in "-_") or "audio"
        extra = {"Accept-Ranges": "bytes", "Content-Length": str(length),
                 "Content-Disposition": f'inline; filename="{safe}.wav"'}
        if status == 206:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._hdr(status, "audio/wav", extra)
        try:
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse(self):
        global _sse_count
        with _sse_lock:
            if _sse_count >= MAX_SSE:
                return self._json({"error": "too many streams"}, 429)
            _sse_count += 1
        try:
            self._hdr(200, "text/event-stream", {"Cache-Control": "no-cache"})
            # Cap a single stream at 10 min so an abandoned-but-silent client (no RST) can't
            # hold one of the MAX_SSE slots forever; EventSource auto-reconnects, so the live
            # view is unaffected and the slot is freed on the next status write.
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                payload = json.dumps(ENGINE.status(), ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _sse_lock:
                _sse_count -= 1


def main(argv=None) -> int:
    global TOKEN, ALLOWED_HOSTS
    lan = os.environ.get("VOXTERM_GUI_LAN") == "1"
    host = "0.0.0.0" if lan else "127.0.0.1"
    port = int(os.environ.get("VOXTERM_GUI_PORT", DEFAULT_PORT))
    if lan:
        # Records a real room — never expose to the wifi without a secret.
        TOKEN = os.environ.get("VOXTERM_GUI_TOKEN") or secrets.token_urlsafe(24)
        ALLOWED_HOSTS = None  # token-gated; LAN IP/hostname varies, so no host allowlist
    else:
        # Loopback: enforce a Host allowlist so a rebinding site can't drive the tokenless API.
        ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}
        # Defense-in-depth: when a launcher / Tauri shell mints a token, require it even on
        # loopback — this closes the co-resident local-process hole. Bare
        # `python -m gui.server` sets no token, so loopback stays open exactly as before
        # (zero regression). A same-UID process can still read the token from the
        # environment; that is conceded, not defended.
        _loopback_token = os.environ.get("VOXTERM_GUI_TOKEN")
        if _loopback_token:
            TOKEN = _loopback_token
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    ENGINE.warm()   # background preload of the default model so the first recording is snappy
    if lan:
        print(f"[voxterm-gui] LAN-exposed (VOXTERM_GUI_LAN=1) — token REQUIRED on every /api call.", flush=True)
        print(f"[voxterm-gui] open from your phone:  http://<this-host>:{port}/?token={TOKEN}", flush=True)
    else:
        print(f"[voxterm-gui] serving http://127.0.0.1:{port}  (loopback only; set VOXTERM_GUI_LAN=1 for phone access)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
