"""VoxTerm transcript collector — FastAPI app.

Receives transcripts (and optional audio) from VoxTerm clients via HTTP POST.
Stores blobs on the filesystem under VOXTERM_SERVER_DATA_DIR/uploads/<session_id>/
and indexes them in a SQLite database.

v1: NO AUTH. Refuses to bind to non-loopback unless VOXTERM_ALLOW_PUBLIC=1.
See README.md for the production-readiness checklist.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from server.store import TranscriptStore


# ── Storage configuration ─────────────────────────────────────────

DATA_DIR = Path(os.environ.get("VOXTERM_SERVER_DATA_DIR", "./server-data")).resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "transcripts.db"

# Tight perms for sensitive uploads. Owner-only by default.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# Streaming chunk size for multipart bodies — keeps RAM bounded for hour-long WAVs.
_STREAM_CHUNK = 1024 * 1024  # 1 MiB

# Per-session locks so two retries of the same session_id can't interleave
# their disk writes / DB upsert.
_SESSION_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
_LOCKS_GUARD = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _SESSION_LOCKS[session_id]


# Allow only the formats VoxTerm clients actually send, plus ASCII-safe ids.
# This forbids `.`, `..`, slashes, backslashes, control chars, etc.
_VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9_\-:]{1,128}$")


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _VALID_SESSION_ID.match(session_id):
        raise HTTPException(400, "metadata.session_id is missing or has illegal characters")


def _safe_int(value, field: str) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, f"metadata.{field} must be an integer")


def _public_record(row: dict) -> dict:
    """Strip absolute filesystem paths from API responses; keep only sizes/booleans."""
    if not row:
        return row
    out = {k: v for k, v in row.items() if k not in ("transcript_path", "audio_path")}
    out["has_audio"] = bool(row.get("audio_path"))
    return out


def _stream_to_file(upload: UploadFile, dest: Path) -> int:
    """Stream an UploadFile to disk in fixed-size chunks. Returns bytes written."""
    total = 0
    # `os.open` lets us set the mode atomically (avoids a brief 0644 window).
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = upload.file.read(_STREAM_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
    except BaseException:
        # Don't leave a partial file behind on error
        try:
            dest.unlink()
        except FileNotFoundError:
            pass
        raise
    return total


def _write_text_secure(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def _make_app(store: TranscriptStore | None = None) -> FastAPI:
    """Build a FastAPI app. Allows injecting a store for tests."""
    UPLOADS_DIR.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    try:
        os.chmod(UPLOADS_DIR, _DIR_MODE)
    except OSError:
        pass
    if store is None:
        store = TranscriptStore(DB_PATH)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Loopback safeguard for the case where someone runs us via `uvicorn server.app:app`
        # directly (bypassing main()). FastAPI doesn't expose the bind host, so we read
        # uvicorn's environment / sys.argv heuristically.
        _refuse_if_public_at_startup()
        yield

    app = FastAPI(
        title="VoxTerm Collector",
        description="Receives transcripts + audio from VoxTerm clients.",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.store = store

    # ── Routes ─────────────────────────────────────────────────

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/v1/transcripts")
    async def upload_transcript(
        metadata: str = Form(...),
        transcript: UploadFile = File(...),
        audio: UploadFile | None = File(None),
    ) -> JSONResponse:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"invalid metadata JSON: {e}")
        if not isinstance(meta, dict):
            raise HTTPException(400, "metadata must be a JSON object")

        session_id = meta.get("session_id")
        _validate_session_id(session_id)
        entry_count = _safe_int(meta.get("entry_count"), "entry_count")

        with _session_lock(session_id):
            session_dir = UPLOADS_DIR / session_id
            session_dir.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
            try:
                os.chmod(session_dir, _DIR_MODE)
            except OSError:
                pass

            transcript_path = session_dir / "transcript.md"
            transcript_bytes = _stream_to_file(transcript, transcript_path)

            _write_text_secure(
                session_dir / "metadata.json",
                json.dumps(meta, indent=2),
            )

            audio_path: Path | None = None
            audio_bytes_count: int | None = None
            existing_audio = session_dir / "audio.wav"
            if audio is not None:
                audio_path = existing_audio
                audio_bytes_count = _stream_to_file(audio, audio_path)
            else:
                # Re-upload without audio: clear any stale WAV from a prior version
                if existing_audio.exists():
                    try:
                        existing_audio.unlink()
                    except OSError:
                        pass

            app.state.store.upsert(
                session_id=session_id,
                hostname=str(meta.get("hostname", "")),
                model_name=str(meta.get("model_name", "")),
                language=str(meta.get("language", "")),
                started_at=str(meta.get("started_at", "")),
                ended_at=str(meta.get("ended_at", "")),
                entry_count=entry_count,
                voxterm_version=str(meta.get("voxterm_version", "")),
                transcript_path=str(transcript_path),
                audio_path=str(audio_path) if audio_path else None,
                audio_bytes=audio_bytes_count,
            )

        return JSONResponse({
            "ok": True,
            "session_id": session_id,
            "transcript_bytes": transcript_bytes,
            "audio_bytes": audio_bytes_count,
        })

    @app.get("/v1/transcripts")
    async def list_transcripts(limit: int = 50, offset: int = 0) -> dict:
        if limit < 1 or limit > 500:
            raise HTTPException(400, "limit must be between 1 and 500")
        if offset < 0:
            raise HTTPException(400, "offset must be >= 0")
        rows = app.state.store.list(limit=limit, offset=offset)
        return {"items": [_public_record(r) for r in rows]}

    @app.get("/v1/transcripts/{session_id}")
    async def get_transcript(session_id: str) -> dict:
        _validate_session_id(session_id)
        record = app.state.store.get(session_id)
        if record is None:
            raise HTTPException(404, "session not found")
        return _public_record(record)

    @app.get("/v1/transcripts/{session_id}/audio")
    async def get_audio(session_id: str):
        _validate_session_id(session_id)
        record = app.state.store.get(session_id)
        if record is None:
            raise HTTPException(404, "session not found")
        audio_path = record.get("audio_path")
        if not audio_path or not Path(audio_path).exists():
            raise HTTPException(404, "audio not available for this session")
        return FileResponse(
            audio_path,
            media_type="audio/wav",
            filename=f"{session_id}.wav",
        )

    return app


app = _make_app()


# ── Loopback safeguard ────────────────────────────────────────────

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _refuse_if_public(host: str) -> None:
    """Hard-refuse non-loopback binds unless explicitly allowed."""
    if host in _LOOPBACK_HOSTS:
        return
    if os.environ.get("VOXTERM_ALLOW_PUBLIC") == "1":
        return
    raise SystemExit(
        f"\nVOXTERM SERVER refused to bind {host}.\n"
        "v1 has no auth. Set VOXTERM_ALLOW_PUBLIC=1 only after you've added\n"
        "auth, TLS, and rate limiting (see server/README.md).\n"
    )


def _refuse_if_public_at_startup() -> None:
    """Best-effort safeguard for direct `uvicorn server.app:app` invocations.

    main() is the canonical entry point and does the host check up front, but
    when someone bypasses it we read the same signals uvicorn does (env vars
    documented at https://www.uvicorn.org and the --host argv slot) and bail
    if the host looks public. It's not bulletproof — uvicorn's `--host` could
    be supplied via a config file we don't parse — but it catches the
    everyday `uvicorn server.app:app --host 0.0.0.0` mistake the README
    cautions against.
    """
    if os.environ.get("VOXTERM_ALLOW_PUBLIC") == "1":
        return
    host = os.environ.get("UVICORN_HOST")
    if host is None:
        import sys
        argv = sys.argv
        if "--host" in argv:
            i = argv.index("--host")
            if i + 1 < len(argv):
                host = argv[i + 1]
    if host is None:
        return  # nothing to check; main() didn't run, but no public host either
    _refuse_if_public(host)


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="VoxTerm transcript collector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    _refuse_if_public(args.host)
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
