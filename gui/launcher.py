"""Open the VoxTerm web GUI from the terminal.

`voxterm-gui`, `voxterm gui`, and the TUI `g` key all land here. It starts the local
engine on a loopback port with a fresh per-run token and opens it in your browser —
nothing leaves your machine. The native Tauri desktop app is a separate, self-contained
entry point (it spawns its own engine); this launcher is the universal browser path.
"""
from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
import urllib.request


def _free_loopback_port() -> int:
    """Ask the OS for an unused loopback port, then release it for the server to claim."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serving(port: int, token: str) -> bool:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/options",
        headers={"X-VoxTerm-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _open_browser_when_ready(port: int, token: str, timeout: float = 60.0) -> None:
    import webbrowser
    url = f"http://127.0.0.1:{port}/?token={token}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _serving(port, token):
            webbrowser.open(url)
            return
        time.sleep(0.25)
    print(f"[voxterm-gui] engine slow to start — open {url} manually.", file=sys.stderr, flush=True)


def main(argv=None) -> int:
    # Always loopback; mint a fresh token unless one was handed in (honored by
    # gui.server's loopback branch, so the local API is token-gated, not open).
    port = int(os.environ.get("VOXTERM_GUI_PORT") or _free_loopback_port())
    token = os.environ.get("VOXTERM_GUI_TOKEN") or secrets.token_urlsafe(24)
    os.environ["VOXTERM_GUI_PORT"] = str(port)
    os.environ["VOXTERM_GUI_TOKEN"] = token
    os.environ.pop("VOXTERM_GUI_LAN", None)  # the launcher is loopback-only by definition

    print(f"[voxterm-gui] starting the local engine — your browser will open at "
          f"http://127.0.0.1:{port}", flush=True)
    threading.Thread(target=_open_browser_when_ready, args=(port, token), daemon=True).start()

    from gui.server import main as server_main
    try:
        return server_main()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
