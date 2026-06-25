"""Tests for Dialogues Control Plane HTTP helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import urllib.error

from dialogues.config import USER_AGENT
from dialogues.http import cp_json_headers, format_cp_http_error
from dialogues.oauth_loopback import OAuthCallbackError, exchange_code
from dialogues.topos_client import post_app_ingest
from dialogues.credentials import DialoguesCredentials


def test_cp_json_headers_include_user_agent():
    headers = cp_json_headers()
    assert headers["User-Agent"] == USER_AGENT
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"


def test_cp_json_headers_authorization():
    headers = cp_json_headers(authorization="Bearer tok")
    assert headers["Authorization"] == "Bearer tok"


def test_format_cp_http_error_browser_signature_banned():
    detail = json.dumps(
        {
            "error_code": 1010,
            "error_name": "browser_signature_banned",
            "title": "Error 1010: Access denied",
        }
    )
    msg = format_cp_http_error(status=403, detail=detail, action="Grant exchange")
    assert "Error 1010" in msg
    assert USER_AGENT in msg


def test_exchange_code_sends_user_agent(monkeypatch):
    captured: dict[str, str] = {}

    class FakeResp:
        def read(self) -> bytes:
            return b'{"plugin_attach_token":"tok","resource_id":"res"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30):
        captured["user_agent"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr("dialogues.oauth_loopback.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("dialogues.oauth_loopback.save_credentials", MagicMock())
    creds = exchange_code(code="auth-code", code_verifier="verifier", cp_url="https://cp.example.com")
    assert captured["user_agent"] == USER_AGENT
    assert creds.plugin_attach_token == "tok"


def test_post_app_ingest_sends_user_agent(monkeypatch):
    captured: dict[str, str] = {}

    class FakeResp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=10):
        captured["user_agent"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr("dialogues.topos_client.urllib.request.urlopen", fake_urlopen)
    creds = DialoguesCredentials(
        plugin_attach_token="tok",
        resource_id="res",
        control_plane_url="https://cp.example.com",
    )
    post_app_ingest(creds, [{"message_id": "m1", "content": "hi"}])
    assert captured["user_agent"] == USER_AGENT


def test_exchange_code_maps_cloudflare_1010(monkeypatch):
    body = json.dumps({"error_code": 1010, "error_name": "browser_signature_banned"}).encode("utf-8")

    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=__import__("io").BytesIO(body),
        )

    monkeypatch.setattr("dialogues.oauth_loopback.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OAuthCallbackError, match="Error 1010"):
        exchange_code(code="auth-code", code_verifier="verifier", cp_url="https://cp.example.com")
