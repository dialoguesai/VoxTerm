"""Torch-free stand-in for gui.server — for the Android smoke test only.

Serves gui/static/ + canned /api/options|status|sessions + a heartbeat SSE, and LOGS every
request so the test can assert the WebView actually loaded and called the API. Loopback +
tokenless (mirrors the real server's TOKEN-is-None loopback mode), zero ML imports, instant boot.

    python scripts/mock_backend.py [--port 8740]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

STATIC = Path(__file__).resolve().parent.parent / "gui" / "static"
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
       ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
       ".json": "application/json", ".webmanifest": "application/manifest+json"}


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        super().log_message(*a)          # ALWAYS log — the smoke test greps these lines

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._serve("index.html")
        if p == "/api/options":
            return self._send(200, "application/json",
                              json.dumps({"models": ["fw-base", "fw-small"],
                                          "languages": {"en": "English"}}).encode())
        if p == "/api/status":
            return self._send(200, "application/json",
                              json.dumps({"recording": False, "level": 0.0, "elapsed": 0,
                                          "job": {"state": "idle"},
                                          "live": {"active": False, "wav": None, "lines": [], "partial": None}}).encode())
        if p == "/api/sessions":
            return self._send(200, "application/json", json.dumps({"sessions": []}).encode())
        if p == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for _ in range(600):     # ~heartbeat; ends when the client disconnects
                    self.wfile.write(b'data: {"recording": false, "level": 0.0, "job": {"state": "idle"}, "live": {"active": false, "lines": [], "partial": null}}\n\n')
                    self.wfile.flush()
                    time.sleep(0.4)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if p.startswith("/static/"):
            return self._serve(p[len("/static/"):])
        if p in ("/sw.js", "/manifest.webmanifest"):
            return self._serve(p.lstrip("/"))
        return self._send(404, "application/json", b'{"error":"not found"}')

    def do_POST(self):
        # accept + acknowledge any control POST so a --deep test can confirm the round trip
        return self._send(200, "application/json", b'{"ok": true, "mock": true}')

    def _serve(self, rel):
        target = (STATIC / rel).resolve()
        try:
            target.relative_to(STATIC.resolve())
        except ValueError:
            return self._send(403, "application/json", b'{"error":"forbidden"}')
        if not target.is_file():
            return self._send(404, "application/json", b'{"error":"not found"}')
        self._send(200, _CT.get(target.suffix, "application/octet-stream"), target.read_bytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8740)
    args = ap.parse_args(argv)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Mock)
    httpd.daemon_threads = True
    print(f"[mock] serving http://127.0.0.1:{args.port} (loopback, tokenless, no torch)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
