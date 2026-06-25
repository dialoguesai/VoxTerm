"""Dialogues integration constants (override via environment)."""

from __future__ import annotations

import os

DEFAULT_CONTROL_PLANE_URL = "https://cp.logu3s.com"
APP_ID = os.environ.get("DIALOGUES_APP_ID", "voxterm")
SOURCE_ID = os.environ.get("DIALOGUES_SOURCE_ID", "voxterm_transcripts")
OAUTH_PORT = int(os.environ.get("DIALOGUES_OAUTH_PORT", "8741"))
OAUTH_REDIRECT_PATH = "/oauth/callback"
OAUTH_SCOPES = os.environ.get("DIALOGUES_SCOPES", "messages:write")
OAUTH_TIMEOUT_SECONDS = float(os.environ.get("DIALOGUES_OAUTH_TIMEOUT", "120"))
USER_AGENT = os.environ.get("VOXTERM_DIALOGUES_USER_AGENT", "dialoguesai/voxterm-grant/0.3.0")


def control_plane_url() -> str:
    return (
        os.environ.get("DIALOGUES_CONTROL_PLANE_URL")
        or os.environ.get("CONTROL_PLANE_URL")
        or DEFAULT_CONTROL_PLANE_URL
    ).rstrip("/")


def redirect_uri() -> str:
    return f"http://127.0.0.1:{OAUTH_PORT}{OAUTH_REDIRECT_PATH}"
