"""SQLite-backed index for uploaded transcripts."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

# Sensitive index — owner-only.
_DB_MODE = 0o600
_DB_DIR_MODE = 0o700


class TranscriptStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, mode=_DB_DIR_MODE, exist_ok=True)
        try:
            os.chmod(self._path.parent, _DB_DIR_MODE)
        except OSError:
            pass
        # Pre-create the file with tight perms so sqlite doesn't open it 0644.
        if not self._path.exists():
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _DB_MODE)
            os.close(fd)
        else:
            try:
                os.chmod(self._path, _DB_MODE)
            except OSError:
                pass
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert(
        self,
        *,
        session_id: str,
        hostname: str,
        model_name: str,
        language: str,
        started_at: str,
        ended_at: str,
        entry_count: int,
        voxterm_version: str,
        transcript_path: str,
        audio_path: str | None,
        audio_bytes: int | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO transcripts (
                    session_id, hostname, model_name, language,
                    started_at, ended_at, entry_count, voxterm_version,
                    uploaded_at, transcript_path, audio_path, audio_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    hostname=excluded.hostname,
                    model_name=excluded.model_name,
                    language=excluded.language,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    entry_count=excluded.entry_count,
                    voxterm_version=excluded.voxterm_version,
                    uploaded_at=excluded.uploaded_at,
                    transcript_path=excluded.transcript_path,
                    audio_path=excluded.audio_path,
                    audio_bytes=excluded.audio_bytes
                """,
                (
                    session_id, hostname, model_name, language,
                    started_at, ended_at, entry_count, voxterm_version,
                    datetime.now(timezone.utc).isoformat(), transcript_path,
                    audio_path, audio_bytes,
                ),
            )
            self._conn.commit()

    def get(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transcripts WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def list(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transcripts ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
