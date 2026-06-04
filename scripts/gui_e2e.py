"""Headless-browser e2e for the VoxTerm GUI (Chrome DevTools Protocol).

Boots the real `gui.server`, drives a headless Chrome through the actual UI, and asserts the
review flow end-to-end: the model dropdown + session list populate from the API, clicking a
past session loads + renders its transcript. Saves a screenshot of the loaded transcript.

This covers the browser flow that unit tests can't (the record-with-a-real-mic path still
needs hardware). Requires: google-chrome + `pip install websocket-client` (dev-only).

    python scripts/gui_e2e.py [--shot /tmp/voxterm-transcript.png]
Exit 0 = all assertions passed.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client (dev dep)

ROOT = Path(__file__).resolve().parent.parent
PORT = 8740
CDP_PORT = 9222


def _wait(url: str, timeout: float = 30.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            return urllib.request.urlopen(url, timeout=2).read()
        except Exception:
            time.sleep(0.4)
    raise TimeoutError(f"timed out waiting for {url}")


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def call(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def poll(self, expr: str, want=True, timeout: float = 20.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.eval(expr) == want:
                return True
            time.sleep(0.3)
        return False


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    shot = "/tmp/voxterm-transcript.png"
    if "--shot" in args:
        shot = args[args.index("--shot") + 1]
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        print("SKIP: no chrome found"); return 3

    server = subprocess.Popen([sys.executable, "-m", "gui.server"], cwd=str(ROOT),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = subprocess.Popen([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                                f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
                                "--window-size=1280,920", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = []
    try:
        _wait(f"http://127.0.0.1:{PORT}/api/options")          # server up
        _wait(f"http://127.0.0.1:{CDP_PORT}/json/version")     # cdp up
        # recent Chrome requires PUT for /json/new (GET → 405)
        _req = urllib.request.Request(
            f"http://127.0.0.1:{CDP_PORT}/json/new?http://127.0.0.1:{PORT}/", method="PUT")
        tab = json.loads(urllib.request.urlopen(_req, timeout=10).read())
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.call("Page.enable"); cdp.call("Runtime.enable")

        if not cdp.poll("document.querySelectorAll('#model option').length > 0"):
            fails.append("model dropdown never populated")
        else:
            opts = cdp.eval("Array.from(document.querySelectorAll('#model option')).map(o=>o.value).join(',')")
            print(f"  models: {opts}")
            if "sherpa" not in (opts or ""):
                print("  ! note: no sherpa key in dropdown (extra not installed?)")
        n_sessions = cdp.eval("document.querySelectorAll('.session').length") or 0
        print(f"  sessions listed: {n_sessions}")
        if n_sessions == 0:
            fails.append("no sessions listed (expected past transcripts)")
        else:
            cdp.eval("document.querySelector('.session').click()")
            ok = cdp.poll("!!document.querySelector('.transcript-view') && "
                          "document.querySelector('.transcript-view').textContent.trim().length > 20")
            if not ok:
                fails.append("transcript did not render after clicking a session")
            else:
                preview = cdp.eval("document.querySelector('.transcript-view').textContent.trim().slice(0,80)")
                print(f"  transcript rendered: {preview!r}…")
                png = cdp.call("Page.captureScreenshot")["data"]
                Path(shot).write_bytes(base64.b64decode(png))
                print(f"  screenshot: {shot}")
    finally:
        browser.terminate()
        server.terminate()

    if fails:
        print("FAIL: " + "; ".join(fails)); return 1
    print("OK: GUI e2e passed (dropdown + sessions + transcript render)"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
