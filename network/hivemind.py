"""Hivemind transcript-sink discovery + publisher (issue #105).

VoxTerm pushes transcript batches to a swf-node that advertises itself
on the LAN as `_shape-rotator-hivemind._tcp.local.`. The wire contract
is locked in `SHAPE-ROTATOR-OS-SPEC.md` §4.3 (in `searxng-wth-frnds` /
shape-rotator-wrld-knwldge-viz). VoxTerm clients DO NOT sign or
encrypt — the convent-box sink resigns the wrapped bundle.

Public surface:
    HIVEMIND_SERVICE_TYPE
    Sink
    HivemindBrowser     — mDNS browser; emits Sink lists on change
    HivemindClient      — batches segments, POSTs to a chosen Sink
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger("voxterm.hivemind")

#: Locked in spec §4.3.
HIVEMIND_SERVICE_TYPE = "_shape-rotator-hivemind._tcp.local."

#: Flush triggers per issue #105.
FLUSH_SECONDS = 60.0
FLUSH_SEGMENTS = 30

#: Per-batch HTTP timeout. Generous because mDNS-discovered hosts can be
#: behind a Wi-Fi handoff hiccup; we'd rather wait than drop the batch.
POST_TIMEOUT_SECONDS = 10.0

#: Body cap mirrored from swf-node's route.py (1 MiB). We won't hit this
#: with normal transcription (30 segs × low-kB each), but a runaway
#: producer shouldn't be able to OOM the sink.
_MAX_BATCH_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True)
class Sink:
    """A discovered hivemind sink. `pubkey` is the stable pin — voxterm
    re-finds the same sink across restarts by matching this."""

    pubkey: str
    name: str
    host: str
    port: int
    proto: str = "shape-rotator-hivemind/v1"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/hivemind/transcripts"

    @property
    def display(self) -> str:
        """Short label for the picker UI."""
        short = self.pubkey[:8] if self.pubkey else "?"
        return f"{self.name or self.host}  ({short}…)"


# ── mDNS browser ─────────────────────────────────────────────────────────


class HivemindBrowser:
    """Browses the LAN for hivemind sinks.

    The on_change callback fires from the zeroconf thread whenever the
    set of discovered sinks changes — UI consumers should marshal onto
    their own event loop (Textual's `call_from_thread`).
    """

    def __init__(
        self,
        on_change: Callable[[list[Sink]], None] | None = None,
    ) -> None:
        self._on_change = on_change
        self._sinks: dict[str, Sink] = {}  # service-name → Sink
        self._lock = threading.Lock()
        self._zc = None
        self._browser = None

    def start(self) -> None:
        try:
            from zeroconf import IPVersion, ServiceBrowser, Zeroconf
        except ImportError:
            log.warning("zeroconf unavailable — hivemind discovery disabled")
            return

        try:
            self._zc = Zeroconf(ip_version=IPVersion.V4Only)
            self._browser = ServiceBrowser(
                self._zc,
                HIVEMIND_SERVICE_TYPE,
                handlers=[self._on_state_change],
            )
        except OSError as exc:
            # Port 5353 in use, no network, etc. Don't kill the app.
            log.warning("hivemind browser failed to start: %s", exc)
            self._zc = None
            self._browser = None

    def stop(self) -> None:
        try:
            if self._browser is not None:
                self._browser.cancel()
        except Exception:
            pass
        try:
            if self._zc is not None:
                self._zc.close()
        except Exception:
            pass
        self._zc = None
        self._browser = None

    def sinks(self) -> list[Sink]:
        with self._lock:
            return sorted(self._sinks.values(), key=lambda s: s.name)

    def _on_state_change(self, zeroconf, service_type, name, state_change):
        # Imported lazily so the module imports cleanly on systems
        # without zeroconf (e.g. constrained CI).
        from zeroconf import ServiceStateChange

        if state_change in (
            ServiceStateChange.Added,
            ServiceStateChange.Updated,
        ):
            try:
                info = zeroconf.get_service_info(service_type, name)
            except Exception:
                info = None
            if info is None:
                return
            sink = self._parse(info, name)
            if sink is None:
                return
            with self._lock:
                self._sinks[name] = sink
            self._fire_change()
        elif state_change == ServiceStateChange.Removed:
            with self._lock:
                if name in self._sinks:
                    self._sinks.pop(name, None)
                    fire = True
                else:
                    fire = False
            if fire:
                self._fire_change()

    def _fire_change(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.sinks())
        except Exception as exc:
            log.warning("hivemind on_change callback raised: %s", exc)

    @staticmethod
    def _parse(info, service_name: str) -> Sink | None:
        """Extract a Sink from a zeroconf ServiceInfo. Returns None if
        the TXT record is missing required fields — we'd rather skip a
        misconfigured sink than panic the browser thread."""
        try:
            props = info.properties or {}
            pubkey_b = props.get(b"pubkey") or b""
            pubkey = pubkey_b.decode("ascii", errors="replace").strip()
            proto = (props.get(b"proto") or b"").decode("ascii", errors="replace")
            node = (props.get(b"node") or b"").decode("ascii", errors="replace")

            addrs = info.addresses or []
            if not addrs:
                return None
            host = socket.inet_ntoa(addrs[0])

            port = int(info.port or 0)
            if port <= 0:
                return None

            # We tolerate empty pubkey (skip the pin), but log loudly.
            if not pubkey:
                log.warning(
                    "hivemind sink %s advertised without pubkey — pinning disabled",
                    service_name,
                )

            display_name = node or service_name.split(".", 1)[0]
            return Sink(
                pubkey=pubkey,
                name=display_name,
                host=host,
                port=port,
                proto=proto or "shape-rotator-hivemind/v1",
            )
        except Exception as exc:
            log.warning("failed to parse hivemind ServiceInfo: %s", exc)
            return None


# ── client / publisher ───────────────────────────────────────────────────


def _iso_z(ts: float | None = None) -> str:
    """RFC3339 / ISO-8601 UTC with `Z` suffix. Spec §3.5 + sink validator
    accept any non-empty string but this is the canonical form."""
    if ts is None:
        ts = time.time()
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class _Batch:
    started_at_ts: float
    started_at_iso: str
    segments: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class PublishResult:
    """Outcome of one batch publish attempt. Surfaced via the
    `on_publish` callback so the UI can render confirmation / failure
    log lines. Caller is responsible for marshaling onto its own thread
    (the callback fires from the publishing thread)."""

    ok: bool
    record_id: str
    batch_index: int
    segment_count: int
    sink_name: str
    error: str | None = None  # filled when ok=False; short human-readable cause


class HivemindClient:
    """Batches transcribed segments and POSTs to a Sink.

    Thread-safe. add_segment is callable from the transcription worker;
    a daemon flusher checks the time-based trigger every second; flush
    POSTs run on the calling thread (so the timer's POST runs on the
    timer thread; explicit flush() runs on the caller's thread).

    Encapsulation per issue #105: external callers go through three
    methods only — begin_record, add_segment, close. The swf-node
    coupling lives in `_publish_batch`; swap that out and nothing else.
    """

    def __init__(
        self,
        sink: Sink,
        device_id: str,
        *,
        location: str = "",
        flush_seconds: float = FLUSH_SECONDS,
        flush_segments: int = FLUSH_SEGMENTS,
        on_publish: Callable[[PublishResult], None] | None = None,
    ) -> None:
        self.sink = sink
        self.device_id = device_id
        self.location = location
        self._flush_seconds = flush_seconds
        self._flush_segments = flush_segments
        self._on_publish = on_publish

        self._lock = threading.Lock()
        self._record_id: str | None = None
        self._batch_index: int = 0
        self._batch: _Batch | None = None
        self._closed = False

        self._timer_stop = threading.Event()
        self._timer = threading.Thread(
            target=self._flush_timer_loop,
            name="hivemind-flush-timer",
            daemon=True,
        )
        self._timer.start()

    # ── public API ────────────────────────────────────────────────────

    def begin_record(self, record_id: str) -> None:
        """Start a new meeting. Resets batch_index to 0 and flushes any
        pending segments from the previous record under the previous
        record_id (so segments don't bleed across meetings)."""
        with self._lock:
            stale = self._take_batch_locked()
            stale_record = self._record_id
            stale_index = self._batch_index
            self._record_id = record_id
            self._batch_index = 0
        if stale is not None and stale_record is not None:
            self._publish_safely(stale, stale_record, stale_index)

    def add_segment(self, t: float, speaker: str, text: str) -> None:
        """Append a transcribed segment to the current batch. `t` is
        seconds since the meeting's record start (NOT batch start) — the
        spec says receivers reconstruct the full transcript by ordering
        batches by batch_index, so segment t-values are meeting-relative."""
        if not text:
            return
        ready_batch: _Batch | None = None
        ready_record: str | None = None
        ready_index: int = 0
        with self._lock:
            if self._closed or self._record_id is None:
                return
            if self._batch is None:
                now = time.time()
                self._batch = _Batch(
                    started_at_ts=now,
                    started_at_iso=_iso_z(now),
                )
            self._batch.segments.append({
                "t": float(t),
                "speaker": str(speaker),
                "text": str(text),
            })
            if len(self._batch.segments) >= self._flush_segments:
                ready_batch = self._take_batch_locked()
                ready_record = self._record_id
                ready_index = self._batch_index
                self._batch_index += 1
        if ready_batch is not None and ready_record is not None:
            self._publish_safely(ready_batch, ready_record, ready_index)

    def flush(self) -> None:
        """Manually flush any pending segments. Use on EOF / record stop
        / shutdown. Safe to call when the batch is empty."""
        with self._lock:
            ready = self._take_batch_locked()
            record = self._record_id
            index = self._batch_index
            if ready is not None:
                self._batch_index += 1
        if ready is not None and record is not None:
            self._publish_safely(ready, record, index)

    def close(self) -> None:
        """Shutdown: flush any pending segments and stop the timer."""
        self.flush()
        self._timer_stop.set()
        with self._lock:
            self._closed = True

    # ── internals ─────────────────────────────────────────────────────

    def _take_batch_locked(self) -> _Batch | None:
        """Caller MUST hold self._lock. Returns the current batch and
        nulls the slot. Returns None if there's nothing pending."""
        b = self._batch
        if b is None or not b.segments:
            self._batch = None
            return None
        self._batch = None
        return b

    def _flush_timer_loop(self) -> None:
        """Daemon loop: every second, check whether the current batch has
        aged past the time-based trigger. If yes, snapshot + publish."""
        while not self._timer_stop.wait(1.0):
            ready: _Batch | None = None
            record: str | None = None
            index = 0
            with self._lock:
                if self._closed:
                    return
                if (
                    self._batch is not None
                    and self._batch.segments
                    and (time.time() - self._batch.started_at_ts)
                        >= self._flush_seconds
                ):
                    ready = self._take_batch_locked()
                    record = self._record_id
                    index = self._batch_index
                    self._batch_index += 1
            if ready is not None and record is not None:
                self._publish_safely(ready, record, index)

    def _publish_safely(
        self,
        batch: _Batch,
        record_id: str,
        batch_index: int,
    ) -> None:
        """Wrapper around `_publish_batch` that swallows network errors —
        per #105, sink unreachable is logged and we keep going (local
        files are still written by the rest of voxterm). Always emits a
        PublishResult to `on_publish` so the UI can show a confirmation
        or failure line per batch attempt."""
        seg_count = len(batch.segments)
        try:
            error = self._publish_batch(batch, record_id, batch_index)
        except Exception as exc:
            error = f"unexpected: {exc}"
            log.warning(
                "hivemind publish raised (record=%s batch=%d sink=%s): %s",
                record_id, batch_index, self.sink.host, exc,
            )
        self._emit_result(
            PublishResult(
                ok=error is None,
                record_id=record_id,
                batch_index=batch_index,
                segment_count=seg_count,
                sink_name=self.sink.name,
                error=error,
            )
        )

    def _emit_result(self, result: PublishResult) -> None:
        if self._on_publish is None:
            return
        try:
            self._on_publish(result)
        except Exception as exc:
            log.warning("hivemind on_publish callback raised: %s", exc)

    def _publish_batch(
        self,
        batch: _Batch,
        record_id: str,
        batch_index: int,
    ) -> str | None:
        """The swappable entry point per issue #105's encapsulation
        requirement. POSTs the canonical voxterm transcript-batch payload
        per spec §4.3. Returns None on success, or a short error tag
        when the POST fails (so the caller can surface it to the UI)."""
        payload = {
            "record_id": record_id,
            "batch_index": batch_index,
            "started_at": batch.started_at_iso,
            "ended_at": _iso_z(),
            "origin_device": self.device_id,
            "segments": batch.segments,
        }
        if self.location:
            payload["location"] = self.location

        body = json.dumps(payload).encode("utf-8")
        if len(body) > _MAX_BATCH_BYTES:
            # Should never happen at the spec's flush triggers, but guard
            # so we don't fire a request that's definitely going to 413.
            log.warning(
                "hivemind batch over 1 MiB (%d bytes) — dropping",
                len(body),
            )
            return "batch over 1 MiB"

        req = urllib.request.Request(
            self.sink.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=POST_TIMEOUT_SECONDS) as resp:
                code = resp.status
                if code != 201:
                    return f"sink returned HTTP {code}"
                return None
        except urllib.error.HTTPError as exc:
            # 4xx / 5xx — give up on this batch. No backup sink, no retry
            # (explicit non-goal in #105). On 409 the sink's view of
            # batch_index has diverged from ours — we *could* re-derive,
            # but the issue says "log + keep going" and the alchemist
            # can re-record if it matters.
            log.warning(
                "hivemind sink rejected batch %d for %s: HTTP %s",
                batch_index, record_id, exc.code,
            )
            return f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            log.warning(
                "hivemind sink unreachable (%s): %s",
                self.sink.host, exc.reason,
            )
            return f"unreachable ({exc.reason})"


# ── meeting-id helper ────────────────────────────────────────────────────


def make_record_id(
    *,
    location: str = "",
    device_id: str = "",
    started_at: float | None = None,
) -> str:
    """Build a `record_id` per the issue's documented format:
    `transcript-<YYYY-MM-DD-HHMM>-<location-or-deviceid-suffix>`.

    The suffix uses `location` when set, otherwise the last 6 chars of
    the device UUID — short enough to fit, long enough to disambiguate
    across devices in a single cohort."""
    if started_at is None:
        started_at = time.time()
    stamp = datetime.fromtimestamp(started_at, tz=timezone.utc).strftime(
        "%Y-%m-%d-%H%M",
    )
    if location:
        suffix = location
    elif device_id:
        suffix = device_id.replace("-", "")[-6:]
    else:
        suffix = "unknown"
    return f"transcript-{stamp}-{suffix}"
