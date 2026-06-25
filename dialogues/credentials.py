"""Persist Dialogues Grant Access credentials locally."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import DATA_DIR

from .config import control_plane_url

_LOCK = threading.Lock()
_FILENAME = "dialogues_credentials.json"


@dataclass
class DialoguesCredentials:
    plugin_attach_token: str
    resource_id: str
    control_plane_url: str


def _credentials_path() -> Path:
    return Path(DATA_DIR) / _FILENAME


def load_credentials() -> Optional[DialoguesCredentials]:
    path = _credentials_path()
    with _LOCK:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    token = str(raw.get("plugin_attach_token") or "").strip()
    resource_id = str(raw.get("resource_id") or "").strip()
    cp = str(raw.get("control_plane_url") or control_plane_url()).strip()
    if not token or not resource_id:
        return None
    return DialoguesCredentials(
        plugin_attach_token=token,
        resource_id=resource_id,
        control_plane_url=cp.rstrip("/"),
    )


def save_credentials(
    *,
    plugin_attach_token: str,
    resource_id: str,
    cp_url: str | None = None,
) -> None:
    path = _credentials_path()
    payload = {
        "plugin_attach_token": plugin_attach_token.strip(),
        "resource_id": resource_id.strip(),
        "control_plane_url": (cp_url or control_plane_url()).rstrip("/"),
    }
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def clear_credentials() -> None:
    path = _credentials_path()
    with _LOCK:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
