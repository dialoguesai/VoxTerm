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
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from server.store import TranscriptStore


# ── Storage configuration ─────────────────────────────────────────

DATA_DIR = Path(os.environ.get("VOXTERM_SERVER_DATA_DIR", "./server-data")).resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "transcripts.db"


def _make_app(store: Optional[TranscriptStore] = None) -> FastAPI:
    """Build a FastAPI app. Allows injecting a store for tests."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if store is None:
        store = TranscriptStore(DB_PATH)

    app = FastAPI(
        title="VoxTerm Collector",
        description="Receives transcripts + audio from VoxTerm clients.",
        version="0.1.0",
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
        audio: Optional[UploadFile] = File(None),
    ) -> JSONResponse:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"invalid metadata JSON: {e}")

        session_id = meta.get("session_id")
        if not session_id or not isinstance(session_id, str):
            raise HTTPException(400, "metadata.session_id is required")
        if "/" in session_id or ".." in session_id or "\\" in session_id:
            raise HTTPException(400, "metadata.session_id contains illegal characters")

        session_dir = UPLOADS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = session_dir / "transcript.md"
        transcript_bytes = await transcript.read()
        transcript_path.write_bytes(transcript_bytes)

        # Persist metadata sidecar so clients/admins can re-derive index from disk
        (session_dir / "metadata.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        audio_path: Optional[Path] = None
        audio_bytes_count: Optional[int] = None
        if audio is not None:
            audio_path = session_dir / "audio.wav"
            data = await audio.read()
            audio_path.write_bytes(data)
            audio_bytes_count = len(data)

        app.state.store.upsert(
            session_id=session_id,
            hostname=meta.get("hostname", ""),
            model_name=meta.get("model_name", ""),
            language=meta.get("language", ""),
            started_at=meta.get("started_at", ""),
            ended_at=meta.get("ended_at", ""),
            entry_count=int(meta.get("entry_count", 0) or 0),
            voxterm_version=meta.get("voxterm_version", ""),
            transcript_path=str(transcript_path),
            audio_path=str(audio_path) if audio_path else None,
            audio_bytes=audio_bytes_count,
        )

        return JSONResponse({
            "ok": True,
            "session_id": session_id,
            "transcript_bytes": len(transcript_bytes),
            "audio_bytes": audio_bytes_count,
        })

    @app.get("/v1/transcripts")
    async def list_transcripts(limit: int = 50, offset: int = 0) -> dict:
        if limit < 1 or limit > 500:
            raise HTTPException(400, "limit must be between 1 and 500")
        if offset < 0:
            raise HTTPException(400, "offset must be >= 0")
        return {"items": app.state.store.list(limit=limit, offset=offset)}

    @app.get("/v1/transcripts/{session_id}")
    async def get_transcript(session_id: str) -> dict:
        record = app.state.store.get(session_id)
        if record is None:
            raise HTTPException(404, "session not found")
        return record

    @app.get("/v1/transcripts/{session_id}/audio")
    async def get_audio(session_id: str):
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
