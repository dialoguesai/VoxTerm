"""HTTP upload of a transcript (and optionally its audio) to the Shape Rotator
collector. Uses stdlib urllib to avoid pulling in a new dependency, matching
the precedent in audio/transcriber.py.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class UploadResult:
    ok: bool
    status: int = 0
    message: str = ""


def _encode_multipart(parts: list[tuple[str, str | bytes, Optional[str], Optional[str]]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body.

    Each part: (field_name, content, filename | None, content_type | None).
    Returns (body_bytes, content_type_header).
    """
    boundary = f"----voxterm-{uuid.uuid4().hex}"
    crlf = b"\r\n"
    chunks: list[bytes] = []
    for name, content, filename, content_type in parts:
        chunks.append(f"--{boundary}".encode())
        if filename is not None:
            disp = f'form-data; name="{name}"; filename="{filename}"'
        else:
            disp = f'form-data; name="{name}"'
        chunks.append(f"Content-Disposition: {disp}".encode())
        if content_type is not None:
            chunks.append(f"Content-Type: {content_type}".encode())
        chunks.append(b"")
        if isinstance(content, str):
            chunks.append(content.encode("utf-8"))
        else:
            chunks.append(content)
    chunks.append(f"--{boundary}--".encode())
    chunks.append(b"")
    body = crlf.join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


def upload_session(
    url: str,
    session_id: str,
    markdown_path: Path,
    audio_path: Optional[Path],
    metadata: dict,
    connect_timeout: float = 10.0,
    read_timeout: float = 60.0,
) -> UploadResult:
    """POST a session's transcript (and optional audio) to the collector.

    Network I/O is synchronous; call this from a daemon thread.
    """
    try:
        markdown_bytes = markdown_path.read_bytes()
    except OSError as e:
        return UploadResult(ok=False, message=f"could not read transcript: {e}")

    parts: list[tuple[str, str | bytes, Optional[str], Optional[str]]] = [
        ("metadata", json.dumps(metadata), "metadata.json", "application/json"),
        ("transcript", markdown_bytes, f"{session_id}.md", "text/markdown"),
    ]
    if audio_path is not None:
        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as e:
            return UploadResult(ok=False, message=f"could not read audio: {e}")
        ctype = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
        parts.append(("audio", audio_bytes, f"{session_id}.wav", ctype))

    body, content_type = _encode_multipart(parts)

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("Content-Length", str(len(body)))

    # urllib uses a single timeout for both connect and read
    timeout = max(connect_timeout, read_timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return UploadResult(ok=True, status=resp.status, message="ok")
    except urllib.error.HTTPError as e:
        return UploadResult(ok=False, status=e.code, message=f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return UploadResult(ok=False, message=f"network error: {e.reason}")
    except socket.timeout:
        return UploadResult(ok=False, message="timeout")
    except Exception as e:
        return UploadResult(ok=False, message=f"upload failed: {e}")


def build_metadata(
    *,
    session_id: str,
    model_name: str,
    language: str,
    started_at: str,
    ended_at: str,
    entry_count: int,
    voxterm_version: str,
) -> dict:
    return {
        "session_id": session_id,
        "hostname": socket.gethostname(),
        "model_name": model_name,
        "language": language,
        "started_at": started_at,
        "ended_at": ended_at,
        "entry_count": entry_count,
        "voxterm_version": voxterm_version,
    }
