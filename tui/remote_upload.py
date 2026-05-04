"""HTTP upload of a transcript (and optionally its audio) to the Shape Rotator
collector. Uses stdlib urllib to avoid pulling in a new dependency, matching
the precedent in audio/transcriber.py.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import socket
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UploadResult:
    ok: bool
    status: int = 0
    message: str = ""


CRLF = b"\r\n"
_AUDIO_CHUNK = 1024 * 1024  # 1 MiB streaming reads — caps RAM use for hour-long WAVs


def _part_header(boundary: str, name: str, filename: str | None, content_type: str | None) -> bytes:
    if filename is not None:
        disp = f'form-data; name="{name}"; filename="{filename}"'
    else:
        disp = f'form-data; name="{name}"'
    lines = [f"--{boundary}", f"Content-Disposition: {disp}"]
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}")
    lines.append("")
    lines.append("")  # trailing blank line + empty so join produces \r\n\r\n
    return CRLF.join(s.encode() for s in lines)


def _build_multipart_to_tempfile(
    boundary: str,
    metadata_json: str,
    transcript_path: Path,
    transcript_name: str,
    audio_path: Path | None,
    audio_name: str | None,
) -> tuple[Path, int]:
    """Stream a multipart body to a temp file. Returns (path, total_bytes).

    Built into a temp file (not memory) so a 100+ MB audio attachment doesn't
    spike RSS during upload. Caller is responsible for unlinking.
    """
    tmp = tempfile.NamedTemporaryFile(
        prefix="voxterm-upload-", suffix=".bin", delete=False,
    )
    try:
        # metadata part
        tmp.write(_part_header(boundary, "metadata", "metadata.json", "application/json"))
        tmp.write(metadata_json.encode("utf-8"))
        tmp.write(CRLF)
        # transcript part
        tmp.write(_part_header(boundary, "transcript", transcript_name, "text/markdown"))
        with transcript_path.open("rb") as src:
            shutil.copyfileobj(src, tmp, _AUDIO_CHUNK)
        tmp.write(CRLF)
        # optional audio part
        if audio_path is not None:
            ctype = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
            tmp.write(_part_header(boundary, "audio", audio_name or audio_path.name, ctype))
            with audio_path.open("rb") as src:
                shutil.copyfileobj(src, tmp, _AUDIO_CHUNK)
            tmp.write(CRLF)
        # closing boundary
        tmp.write(f"--{boundary}--".encode() + CRLF)
        total = tmp.tell()
    finally:
        tmp.close()
    return Path(tmp.name), total


# Test helper kept for unit-test backwards compatibility — small bodies only.
def _encode_multipart(parts: list[tuple[str, str | bytes, str | None, str | None]]) -> tuple[bytes, str]:
    boundary = f"----voxterm-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, content, filename, content_type in parts:
        chunks.append(_part_header(boundary, name, filename, content_type)[:-2])  # strip trailing CRLF (next chunk re-adds)
        if isinstance(content, str):
            chunks.append(content.encode("utf-8"))
        else:
            chunks.append(content)
    chunks.append(f"--{boundary}--".encode())
    body = CRLF.join(chunks) + CRLF
    return body, f"multipart/form-data; boundary={boundary}"


def upload_session(
    url: str,
    session_id: str,
    markdown_path: Path,
    audio_path: Path | None,
    metadata: dict,
    timeout: float = 60.0,
    token: str = "",
) -> UploadResult:
    """POST a session's transcript (and optional audio) to the collector.

    `timeout` is a single value because stdlib urllib has no separate
    connect/read timeouts. Network I/O is synchronous; call this from a
    background thread.

    `token`, if non-empty, is sent as `Authorization: Bearer <token>`.
    The Fileverse sidecar requires this; the FastAPI collector ignores it.
    """
    if not markdown_path.exists():
        return UploadResult(ok=False, message=f"transcript missing: {markdown_path}")
    if audio_path is not None and not audio_path.exists():
        return UploadResult(ok=False, message=f"audio missing: {audio_path}")

    boundary = f"----voxterm-{uuid.uuid4().hex}"
    try:
        body_path, body_size = _build_multipart_to_tempfile(
            boundary,
            json.dumps(metadata),
            markdown_path, f"{session_id}.md",
            audio_path, f"{session_id}.wav" if audio_path else None,
        )
    except OSError as e:
        return UploadResult(ok=False, message=f"could not stage upload body: {e}")

    try:
        with body_path.open("rb") as body:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Content-Length", str(body_size))
            if token:
                req.add_header("Authorization", f"Bearer {token}")
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
    finally:
        try:
            body_path.unlink()
        except OSError:
            pass


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
