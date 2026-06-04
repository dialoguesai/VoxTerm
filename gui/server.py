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

ENGINE = Engine()

# Strict CSP: same-origin only, no external anything (the UI is fully self-hosted).
# style-src allows 'unsafe-inline' because the UI sets element.style (the live level
# ring, the progress bar) and per-speaker color dots; all interpolated values are
# escaped (app.js escapeHtml) and the data is local, so the exposure is minimal.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self'; manifest-src 'self'; "
       "worker-src 'self'; base-uri 'none'; form-action 'none'")
_CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
           ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
           ".png": "image/png", ".webmanifest": "application/manifest+json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "voxterm-gui"

    def _hdr(self, code=200, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._hdr(code, "application/json")
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) or {}
        except Exception:
            return {}

    def log_message(self, *a):  # quiet
        pass

    def _authed(self, q) -> bool:
        """Token check for /api/* when LAN-exposed. Loopback (TOKEN is None) is open."""
        if TOKEN is None:
            return True
        given = (self.headers.get("X-VoxTerm-Token")
                 or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                 or (q.get("token") or [""])[0])
        return bool(given) and secrets.compare_digest(given, TOKEN)

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
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
            return self._json({"models": ENGINE.models(), "languages": ENGINE.languages()})
        if p == "/api/status":
            return self._json(ENGINE.status())
        if p == "/api/sessions":
            return self._json({"sessions": ENGINE.sessions()})
        if p == "/api/session":
            stem = (q.get("stem") or [""])[0]
            kind = (q.get("kind") or ["transcript"])[0]
            d = (q.get("dir") or [None])[0]
            return self._json(ENGINE.read_artifact(stem, kind, dir=d))
        if p == "/api/events":
            return self._sse()
        return self._json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p.startswith("/api/") and not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        if p == "/api/record/start":
            return self._json(ENGINE.start_recording())
        if p == "/api/record/stop":
            b = self._read_json()
            return self._json(ENGINE.stop_recording(model=b.get("model", "fw-small"),
                                                     language=b.get("language", "en")))
        if p == "/api/transcribe":
            b = self._read_json()
            return self._json(ENGINE.transcribe_existing(b.get("wav", ""), model=b.get("model", "fw-small"),
                                                         language=b.get("language", "en")))
        if p == "/api/live/start":
            b = self._read_json()
            return self._json(ENGINE.live_start(b.get("wav")))
        if p == "/api/live/stop":
            return self._json(ENGINE.live_stop())
        if p == "/api/session/delete":
            b = self._read_json()
            return self._json(ENGINE.delete_session(b.get("stem", ""), dir=b.get("dir")))
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
        self._hdr(200, ctype)
        self.wfile.write(data)

    def _sse(self):
        global _sse_count
        with _sse_lock:
            if _sse_count >= MAX_SSE:
                return self._json({"error": "too many streams"}, 429)
            _sse_count += 1
        try:
            self._hdr(200, "text/event-stream", {"Cache-Control": "no-cache", "Connection": "keep-alive"})
            while True:
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
    global TOKEN
    lan = os.environ.get("VOXTERM_GUI_LAN") == "1"
    host = "0.0.0.0" if lan else "127.0.0.1"
    port = int(os.environ.get("VOXTERM_GUI_PORT", DEFAULT_PORT))
    if lan:
        # Records a real room — never expose to the wifi without a secret.
        TOKEN = os.environ.get("VOXTERM_GUI_TOKEN") or secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
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
