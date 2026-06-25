"""Loopback OAuth for Dialogues Grant Access (CLI/TUI pattern)."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from .config import (
    APP_ID,
    OAUTH_PORT,
    OAUTH_REDIRECT_PATH,
    OAUTH_SCOPES,
    OAUTH_TIMEOUT_SECONDS,
    SOURCE_ID,
    control_plane_url,
    redirect_uri,
)
from .credentials import DialoguesCredentials, save_credentials
from .http import cp_json_headers, format_cp_http_error
from .pkce import PkcePair, create_pkce_pair

log = logging.getLogger("voxterm.dialogues.oauth")


@dataclass(frozen=True)
class AttachResult:
    credentials: DialoguesCredentials
    state: str


class OAuthCallbackError(Exception):
    pass


def build_connect_url(*, pkce: PkcePair, state: str, cp_url: str | None = None) -> str:
    base = (cp_url or control_plane_url()).rstrip("/")
    params = urllib.parse.urlencode(
        {
            "app_id": APP_ID,
            "redirect_uri": redirect_uri(),
            "source_id": SOURCE_ID,
            "scopes": OAUTH_SCOPES,
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": pkce.code_challenge_method,
            "force_login": "1",
        }
    )
    return f"{base}/connect?{params}"


def exchange_code(
    *,
    code: str,
    code_verifier: str,
    cp_url: str | None = None,
) -> DialoguesCredentials:
    base = (cp_url or control_plane_url()).rstrip("/")
    body = json.dumps({"code": code, "app_id": APP_ID, "code_verifier": code_verifier}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/connect/exchange",
        data=body,
        headers=cp_json_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OAuthCallbackError(
            format_cp_http_error(status=exc.code, detail=detail, action="Grant exchange")
        ) from exc
    except urllib.error.URLError as exc:
        raise OAuthCallbackError(f"Could not reach Control Plane: {exc}") from exc

    token = str(payload.get("plugin_attach_token") or payload.get("mcp_access_token") or "").strip()
    resource_id = str(payload.get("resource_id") or "").strip()
    if not token or not resource_id:
        raise OAuthCallbackError("Grant exchange did not return token and resource_id")
    creds = DialoguesCredentials(
        plugin_attach_token=token,
        resource_id=resource_id,
        control_plane_url=base,
    )
    save_credentials(
        plugin_attach_token=creds.plugin_attach_token,
        resource_id=creds.resource_id,
        cp_url=creds.control_plane_url,
    )
    return creds


def _wait_for_callback(
    *,
    expected_state: str,
    timeout: float = OAUTH_TIMEOUT_SECONDS,
    port: int = OAUTH_PORT,
) -> tuple[str, str]:
    """Start loopback server; return (code, state_from_url)."""
    result: dict[str, str | None] = {"code": None, "state": None, "error": None}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # noqa: D401
            if log.isEnabledFor(logging.DEBUG):
                log.debug("oauth loopback: " + fmt, *args)

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != OAUTH_REDIRECT_PATH:
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            err = (qs.get("error") or [""])[0]
            if err:
                result["error"] = err
                self._ok_page("Authorization failed. You can close this tab and return to VoxTerm.")
                done.set()
                return
            code = (qs.get("code") or [""])[0]
            state = (qs.get("state") or [""])[0]
            result["code"] = code
            result["state"] = state
            self._ok_page("Dialogues attached. You can close this tab and return to VoxTerm.")
            done.set()

        def _ok_page(self, message: str) -> None:
            body = (
                "<!doctype html><html><body style='font-family:system-ui;"
                "max-width:420px;margin:3rem auto;padding:0 1rem;'>"
                f"<p>{message}</p></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    host = "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="dialogues-oauth")
    thread.start()
    try:
        if not done.wait(timeout=timeout):
            raise OAuthCallbackError(f"Timed out waiting for browser callback ({timeout:.0f}s)")
        if result.get("error"):
            raise OAuthCallbackError(f"Authorization error: {result['error']}")
        code = str(result.get("code") or "").strip()
        state = str(result.get("state") or "").strip()
        if not code:
            raise OAuthCallbackError("Callback did not include an authorization code")
        if state and state != expected_state:
            log.warning("dialogues oauth: state mismatch (continuing exchange)")
        return code, state
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass


def run_attach_flow(
    *,
    open_browser: Callable[[str], bool] | None = None,
    cp_url: str | None = None,
) -> AttachResult:
    """Full attach: PKCE → browser → callback → exchange → persist."""
    pkce = create_pkce_pair()
    state = f"grant-{secrets.token_hex(8)}"
    url = build_connect_url(pkce=pkce, state=state, cp_url=cp_url)
    opener = open_browser or webbrowser.open
    if not opener(url):
        raise OAuthCallbackError("Could not open a web browser for Dialogues login")
    code, _state = _wait_for_callback(expected_state=state)
    creds = exchange_code(code=code, code_verifier=pkce.code_verifier, cp_url=cp_url)
    return AttachResult(credentials=creds, state=state)
