"""Topos / Dialogues app_ingest publisher (parallel to HivemindClient)."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .config import SOURCE_ID
from .credentials import DialoguesCredentials, clear_credentials, load_credentials
from .http import cp_json_headers, format_cp_http_error

log = logging.getLogger("voxterm.dialogues.topos")

FLUSH_SECONDS_DEFAULT = 60.0
FLUSH_SEGMENTS_DEFAULT = 30
POST_TIMEOUT_SECONDS = 10.0


@dataclass
class _PendingSegment:
    t: float
    speaker: str
    text: str


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return s.replace("+00:00", "Z")


def _parse_iso(iso: str) -> datetime:
    text = (iso or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _default_record_id(now: datetime) -> str:
    return "transcript-" + now.strftime("%Y-%m-%d-%H%M%S-voxterm")


def post_app_ingest(
    creds: DialoguesCredentials,
    records: list[dict],
    *,
    source_id: str = SOURCE_ID,
) -> None:
    if not records:
        return
    url = f"{creds.control_plane_url.rstrip('/')}/v1/ingestion/app_ingest"
    body = json.dumps(
        {
            "resource_id": creds.resource_id,
            "source_id": source_id,
            "records": records,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers=cp_json_headers(authorization=f"Bearer {creds.plugin_attach_token}"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT_SECONDS) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in (401, 403):
            try:
                payload = json.loads(detail)
            except json.JSONDecodeError:
                payload = {}
            if payload.get("error_code") != 1010 and payload.get("error_name") != "browser_signature_banned":
                clear_credentials()
                raise PermissionError("Dialogues attachment expired or revoked") from exc
        raise RuntimeError(
            format_cp_http_error(status=exc.code, detail=detail, action="app_ingest")
        ) from exc


def expand_batch_to_records(
    payload: dict,
    *,
    origin_device: str = "",
    location: str = "",
) -> list[dict]:
    """Convert hivemind-shaped batch payload to flat app_ingest records."""
    record_id = str(payload.get("record_id") or _default_record_id(datetime.now(timezone.utc)))
    batch_index = int(payload.get("batch_index") or 0)
    started = _parse_iso(str(payload.get("started_at") or _iso(datetime.now(timezone.utc))))
    segments = payload.get("segments") or []
    loc = str(payload.get("location") or location or "")
    device = str(payload.get("origin_device") or origin_device or "")
    out: list[dict] = []
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        t_offset = float(seg.get("t") or 0.0)
        event_at = _iso(started + timedelta(seconds=t_offset))
        speaker = str(seg.get("speaker") or "?")
        row = {
            "message_id": f"{record_id}:{batch_index}:{idx}",
            "conversation_id": record_id,
            "sender_id": speaker,
            "sender_type": "human",
            "content": text,
            "event_at": event_at,
            "batch_index": batch_index,
            "segment_index": idx,
        }
        if device:
            row["origin_device"] = device
        if loc:
            row["location"] = loc
        out.append(row)
    return out


class ToposClient:
    """Batch transcript segments and POST to Dialogues app_ingest when enabled."""

    def __init__(
        self,
        *,
        record_id: str | None = None,
        device_id: str = "",
        location: str = "",
        flush_seconds: float = FLUSH_SECONDS_DEFAULT,
        flush_segments: int = FLUSH_SEGMENTS_DEFAULT,
        push_enabled: bool = False,
        on_state_change: Callable[[bool], None] | None = None,
        on_batch_posted: Callable[[int, int, str], None] | None = None,
        poster: Callable[[DialoguesCredentials, list[dict]], None] | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._record_id = record_id or _default_record_id(datetime.now(timezone.utc))
        self._device_id = device_id
        self._location = location
        self._flush_seconds = flush_seconds
        self._flush_segments = flush_segments
        self._push_enabled = push_enabled
        self._on_state_change = on_state_change
        self._on_batch_posted = on_batch_posted
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._poster = poster or (lambda c, r: post_app_ingest(c, r))

        self._lock = threading.RLock()
        self._segments: list[_PendingSegment] = []
        self._batch_started_wall: datetime | None = None
        self._batch_started_mono: float | None = None
        self._batch_index = 0
        self._closed = False
        self._batches_sent = 0
        self._batches_dropped = 0
        self._last_error: BaseException | None = None
        self._push_disabled_drop_logged = False
        self._no_creds_logged = False

    @property
    def record_id(self) -> str:
        return self._record_id

    @property
    def push_enabled(self) -> bool:
        return self._push_enabled

    @property
    def batches_sent(self) -> int:
        return self._batches_sent

    @property
    def batches_dropped(self) -> int:
        return self._batches_dropped

    @property
    def is_attached(self) -> bool:
        return load_credentials() is not None

    def enable_push(self) -> None:
        if self._push_enabled:
            return
        self._push_enabled = True
        self._push_disabled_drop_logged = False
        log.info("dialogues: push enabled by user")
        if self._on_state_change is not None:
            try:
                self._on_state_change(True)
            except Exception:
                log.warning("dialogues on_state_change raised", exc_info=True)

    def disable_push(self) -> None:
        if not self._push_enabled:
            return
        self._push_enabled = False
        log.info("dialogues: push disabled by user")
        if self._on_state_change is not None:
            try:
                self._on_state_change(False)
            except Exception:
                log.warning("dialogues on_state_change raised", exc_info=True)

    def add_segment(self, t: float, speaker: str, text: str) -> None:
        if self._closed:
            return
        text = (text or "").strip()
        if not text:
            return
        seg = _PendingSegment(t=float(t), speaker=str(speaker or ""), text=text)
        with self._lock:
            if self._batch_started_wall is None:
                self._batch_started_wall = self._wall_clock()
                self._batch_started_mono = self._clock()
            self._segments.append(seg)
        if self._should_flush():
            self.flush_now()

    def close(self) -> bool:
        if self._closed:
            return False
        flushed = self.flush_now()
        self._closed = True
        return flushed

    def flush_now(self) -> bool:
        with self._lock:
            if not self._segments:
                return False
            payload = self._build_payload_locked()
            self._segments.clear()
            self._batch_started_wall = None
            self._batch_started_mono = None
            self._batch_index += 1

        if not self._push_enabled:
            if not self._push_disabled_drop_logged:
                log.info(
                    "dialogues: push not enabled; %d segs buffered but not POSTed "
                    "(press 'D' in the TUI to enable)",
                    len(payload["segments"]),
                )
                self._push_disabled_drop_logged = True
            self._batches_dropped += 1
            return False
        self._push_disabled_drop_logged = False

        creds = load_credentials()
        if creds is None:
            if not self._no_creds_logged:
                log.warning("dialogues: not attached; dropping batch (%d segs)", len(payload["segments"]))
                self._no_creds_logged = True
            self._batches_dropped += 1
            return False
        self._no_creds_logged = False

        records = expand_batch_to_records(
            payload,
            origin_device=self._device_id,
            location=self._location,
        )
        if not records:
            return False
        try:
            self._poster(creds, records)
            self._batches_sent += 1
            log.info(
                "dialogues: transcript written to TOPOS (record=%s batch=%d %d segments)",
                self._record_id,
                payload["batch_index"],
                len(records),
            )
            if self._on_batch_posted is not None:
                try:
                    self._on_batch_posted(payload["batch_index"], len(records), self._record_id)
                except Exception:
                    log.warning("dialogues on_batch_posted raised", exc_info=True)
            return True
        except PermissionError as exc:
            self._last_error = exc
            self._batches_dropped += 1
            log.warning("dialogues: %s", exc)
            return False
        except Exception as exc:
            self._last_error = exc
            self._batches_dropped += 1
            log.warning("dialogues: POST failed (%s); dropped batch", exc)
            return False

    def _should_flush(self) -> bool:
        with self._lock:
            n = len(self._segments)
            if n == 0:
                return False
            if n >= self._flush_segments:
                return True
            if self._batch_started_mono is None:
                return False
            return (self._clock() - self._batch_started_mono) >= self._flush_seconds

    def _build_payload_locked(self) -> dict:
        ended = self._wall_clock()
        started = self._batch_started_wall or ended
        payload: dict = {
            "record_id": self._record_id,
            "batch_index": self._batch_index,
            "started_at": _iso(started),
            "ended_at": _iso(ended),
            "origin_device": self._device_id,
            "segments": [
                {"t": s.t, "speaker": s.speaker, "text": s.text}
                for s in self._segments
            ],
        }
        if self._location:
            payload["location"] = self._location
        return payload


def configure(
    *,
    device_id: str = "",
    location: str = "",
    record_id: str | None = None,
    push_enabled: bool = False,
    on_state_change: Callable[[bool], None] | None = None,
    on_batch_posted: Callable[[int, int, str], None] | None = None,
) -> ToposClient:
    return ToposClient(
        record_id=record_id,
        device_id=device_id,
        location=location,
        push_enabled=push_enabled,
        on_state_change=on_state_change,
        on_batch_posted=on_batch_posted,
    )
