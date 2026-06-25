"""Shared HTTP helpers for Dialogues Control Plane requests."""

from __future__ import annotations

import json

from .config import USER_AGENT


def cp_json_headers(*, authorization: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if authorization:
        headers["Authorization"] = authorization
    return headers


def format_cp_http_error(*, status: int, detail: str, action: str) -> str:
    """Turn Control Plane / Cloudflare error bodies into a short user message."""
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return f"{action} failed ({status}): {detail}"

    if payload.get("error_name") == "browser_signature_banned" or payload.get("error_code") == 1010:
        return (
            f"{action} failed: Control Plane blocked this client's User-Agent "
            f"(Cloudflare Error 1010). Update VoxTerm and retry; if it persists, "
            f"ask the site owner to allow `{USER_AGENT}` on the Control Plane zone."
        )

    title = str(payload.get("title") or payload.get("detail") or detail).strip()
    return f"{action} failed ({status}): {title}"
