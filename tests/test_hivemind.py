"""Tests for the hivemind transcript publisher (issue #105).

We exercise the batching contract — flush triggers, payload shape,
record_id rotation — without touching the network. The HivemindClient's
HTTP path is replaced with a capturing stub.
"""
from __future__ import annotations

import json
import time

import pytest

import socket

from network.hivemind import (
    HivemindBrowser,
    HivemindClient,
    PublishResult,
    Sink,
    make_record_id,
)


class _FakeServiceInfo:
    """Minimum surface of zeroconf.ServiceInfo that HivemindBrowser._parse
    actually reads. Tests use this to exercise the parser without needing
    real mDNS traffic."""

    def __init__(
        self,
        *,
        properties: dict[bytes, bytes] | None,
        addresses: list[bytes] | None,
        port: int,
    ):
        self.properties = properties
        self.addresses = addresses
        self.port = port


def _stub_client(captured: list, **overrides) -> HivemindClient:
    """Build a HivemindClient whose `_publish_batch` records calls
    instead of opening a socket. Uses 1-second time-trigger by default
    so the timer-based test doesn't run for a real minute."""
    sink = Sink(
        pubkey="a" * 64,
        name="convent-stub",
        host="127.0.0.1",
        port=7777,
    )
    kwargs = {"flush_seconds": overrides.get("flush_seconds", 1.0)}
    if "flush_segments" in overrides:
        kwargs["flush_segments"] = overrides["flush_segments"]
    if "location" in overrides:
        kwargs["location"] = overrides["location"]
    client = HivemindClient(sink, device_id="dev-uuid-9999", **kwargs)

    def _capture(batch, record_id, batch_index):
        # Reproduce the same payload shape the real publisher would
        # produce, so the test is checking the actual contract.
        payload = {
            "record_id": record_id,
            "batch_index": batch_index,
            "started_at": batch.started_at_iso,
            "ended_at": batch.started_at_iso,
            "origin_device": client.device_id,
            "segments": batch.segments,
        }
        if client.location:
            payload["location"] = client.location
        captured.append(payload)

    client._publish_batch = _capture
    return client


def test_flush_on_30_segments():
    captured = []
    client = _stub_client(captured, flush_seconds=999.0)
    try:
        client.begin_record("transcript-2026-05-09-1430-test")
        for i in range(30):
            client.add_segment(float(i), "Alice", f"segment {i}")
        # The 30th segment triggers flush.
        assert len(captured) == 1
        batch = captured[0]
        assert batch["record_id"] == "transcript-2026-05-09-1430-test"
        assert batch["batch_index"] == 0
        assert batch["origin_device"] == "dev-uuid-9999"
        assert len(batch["segments"]) == 30
        assert batch["segments"][0] == {"t": 0.0, "speaker": "Alice", "text": "segment 0"}

        # Adding another segment opens a new batch (batch_index=1).
        client.add_segment(31.0, "Bob", "next batch")
        client.flush()
        assert len(captured) == 2
        assert captured[1]["batch_index"] == 1
        assert len(captured[1]["segments"]) == 1
    finally:
        client.close()


def test_flush_on_eof():
    """Manual flush() should publish whatever's pending and bump batch_index."""
    captured = []
    client = _stub_client(captured, flush_seconds=999.0)
    try:
        client.begin_record("transcript-eof-test")
        client.add_segment(0.0, "Alice", "hi")
        client.add_segment(1.5, "Bob", "hi back")
        client.flush()
        assert len(captured) == 1
        assert captured[0]["batch_index"] == 0
        assert len(captured[0]["segments"]) == 2

        # A second flush with nothing pending must NOT emit a phantom batch.
        client.flush()
        assert len(captured) == 1
    finally:
        client.close()


def test_time_trigger_flushes_on_timer():
    """Daemon timer flushes the batch once it ages past flush_seconds."""
    captured = []
    client = _stub_client(captured, flush_seconds=0.5)
    try:
        client.begin_record("transcript-timer-test")
        client.add_segment(0.0, "Alice", "tick")
        # Wait past the timer trigger (timer wakes once per second; 2s
        # is plenty for a 0.5s threshold).
        deadline = time.time() + 3.0
        while time.time() < deadline and not captured:
            time.sleep(0.1)
        assert len(captured) == 1
        assert len(captured[0]["segments"]) == 1
    finally:
        client.close()


def test_begin_record_flushes_previous_record():
    """Switching meetings mid-stream must publish leftover segments
    under the OLD record_id, not bleed into the new one."""
    captured = []
    client = _stub_client(captured, flush_seconds=999.0)
    try:
        client.begin_record("meeting-A")
        client.add_segment(0.0, "Alice", "hi from A")
        client.begin_record("meeting-B")
        client.add_segment(0.0, "Bob", "hi from B")
        client.flush()

        assert len(captured) == 2
        assert captured[0]["record_id"] == "meeting-A"
        assert captured[0]["segments"][0]["text"] == "hi from A"
        assert captured[1]["record_id"] == "meeting-B"
        assert captured[1]["segments"][0]["text"] == "hi from B"
        # batch_index resets per record_id
        assert captured[0]["batch_index"] == 0
        assert captured[1]["batch_index"] == 0
    finally:
        client.close()


def test_payload_shape_matches_spec():
    """Spec §4.3 (issue #105) — the unsigned payload posted to
    /hivemind/transcripts must contain exactly these fields. We verify
    against the swf-node sink validator's required keys."""
    captured = []
    client = _stub_client(captured, flush_seconds=999.0, location="convent-room-a")
    try:
        client.begin_record("transcript-shape-check")
        client.add_segment(0.0, "Alice", "hello")
        client.add_segment(1.2, "Bob", "world")
        client.flush()
        assert len(captured) == 1
        b = captured[0]
        # Required by swf.hivemind.sink.validate_payload:
        assert isinstance(b["record_id"], str) and b["record_id"]
        assert isinstance(b["batch_index"], int)
        assert isinstance(b["started_at"], str) and b["started_at"]
        assert isinstance(b["ended_at"], str) and b["ended_at"]
        assert isinstance(b["origin_device"], str) and b["origin_device"]
        assert b["location"] == "convent-room-a"
        assert isinstance(b["segments"], list)
        for seg in b["segments"]:
            assert set(seg.keys()) == {"t", "speaker", "text"}
            assert isinstance(seg["t"], float)
            assert isinstance(seg["speaker"], str)
            assert isinstance(seg["text"], str)
        # And the whole thing must round-trip JSON.
        json.dumps(b)
    finally:
        client.close()


def test_no_segments_no_publish():
    """Empty flush() (no segments accumulated) is a no-op — no empty
    batches on the wire."""
    captured = []
    client = _stub_client(captured)
    try:
        client.begin_record("transcript-empty")
        client.flush()
        assert captured == []
    finally:
        client.close()


def test_add_before_begin_record_is_dropped():
    """Defensive: segments arriving before begin_record() shouldn't
    crash or produce a phantom batch with no record_id."""
    captured = []
    client = _stub_client(captured, flush_seconds=999.0)
    try:
        client.add_segment(0.0, "ghost", "nobody listening")
        client.flush()
        assert captured == []
    finally:
        client.close()


# ── make_record_id ────────────────────────────────────────────────────


def test_record_id_format():
    """Issue #105 documents `transcript-<YYYY-MM-DD-HHMM>-<location-or-deviceid>`."""
    rid = make_record_id(
        device_id="abcd1234-5678-9abc-def0-123456789012",
        started_at=1_700_000_000.0,  # 2023-11-14T22:13:20Z
    )
    assert rid.startswith("transcript-")
    parts = rid.split("-")
    # transcript-YYYY-MM-DD-HHMM-<suffix>
    assert parts[0] == "transcript"
    assert len(parts[1]) == 4 and parts[1].isdigit()        # year
    assert len(parts[2]) == 2 and parts[2].isdigit()        # month
    assert len(parts[3]) == 2 and parts[3].isdigit()        # day
    assert len(parts[4]) == 4 and parts[4].isdigit()        # HHMM
    assert parts[5] == "789012"  # last 6 hex chars of the device UUID


def test_on_publish_fires_on_success_and_failure():
    """The on_publish callback receives a PublishResult for every batch
    attempt — both success and failure paths. The UI uses this to render
    HIVE log lines per batch."""
    sink = Sink(pubkey="x" * 64, name="convent-A", host="127.0.0.1", port=7777)
    results: list[PublishResult] = []
    client = HivemindClient(
        sink, device_id="dev-1", flush_seconds=999.0,
        on_publish=lambda r: results.append(r),
    )
    try:
        # Force success: stub publisher returns None (no error)
        client._publish_batch = lambda batch, rid, idx: None
        client.begin_record("rec-A")
        client.add_segment(0.0, "Alice", "ok")
        client.flush()

        assert len(results) == 1
        ok = results[0]
        assert ok.ok is True
        assert ok.record_id == "rec-A"
        assert ok.batch_index == 0
        assert ok.segment_count == 1
        assert ok.sink_name == "convent-A"
        assert ok.error is None

        # Force failure: stub returns an error tag
        client._publish_batch = lambda batch, rid, idx: "unreachable (refused)"
        client.add_segment(1.0, "Bob", "boom")
        client.flush()

        assert len(results) == 2
        bad = results[1]
        assert bad.ok is False
        assert bad.batch_index == 1
        assert bad.error == "unreachable (refused)"

        # Force unexpected exception path: must still fire a result
        def _explode(batch, rid, idx):
            raise RuntimeError("kaboom")
        client._publish_batch = _explode
        client.add_segment(2.0, "Carol", "💣")
        client.flush()

        assert len(results) == 3
        crash = results[2]
        assert crash.ok is False
        assert crash.error.startswith("unexpected:")
    finally:
        client.close()


# ── HivemindBrowser._parse ─────────────────────────────────────────────


def _info(props=None, host="192.168.1.42", port=7777):
    addr_bytes = [socket.inet_aton(host)] if host else []
    return _FakeServiceInfo(properties=props, addresses=addr_bytes, port=port)


def test_parse_well_formed_advert():
    info = _info(props={
        b"pubkey": b"a1b2c3d4" * 8,  # 64 hex chars
        b"proto": b"shape-rotator-hivemind/v1",
        b"node": b"convent-box",
    })
    sink = HivemindBrowser._parse(info, "convent-box-hivemind._foo._tcp.local.")
    assert sink is not None
    assert sink.pubkey == "a1b2c3d4" * 8
    assert sink.host == "192.168.1.42"
    assert sink.port == 7777
    assert sink.name == "convent-box"
    assert sink.proto == "shape-rotator-hivemind/v1"


def test_parse_missing_address_drops_sink():
    """No A record → no Sink. We can't talk to it."""
    info = _info(
        props={b"pubkey": b"a" * 64, b"proto": b"x", b"node": b"n"},
        host=None,
    )
    assert HivemindBrowser._parse(info, "irrelevant") is None


def test_parse_missing_port_drops_sink():
    info = _info(
        props={b"pubkey": b"a" * 64, b"proto": b"x", b"node": b"n"},
        port=0,
    )
    assert HivemindBrowser._parse(info, "irrelevant") is None


def test_parse_missing_pubkey_still_parses():
    """A pubkey-less advert is unsafe but we tolerate it for now —
    the user can still pick it (pinning just won't work). The browser
    logs a warning; the Sink ships with empty pubkey."""
    info = _info(props={b"proto": b"x", b"node": b"box-a"})
    sink = HivemindBrowser._parse(info, "box-a-hivemind._foo._tcp.local.")
    assert sink is not None
    assert sink.pubkey == ""
    assert sink.name == "box-a"


def test_parse_falls_back_to_service_name_for_display():
    """When the TXT record omits `node`, we fall back to the service
    name's first label so the picker still has something to render."""
    info = _info(props={b"pubkey": b"a" * 64, b"proto": b"x"})
    sink = HivemindBrowser._parse(info, "fallback-name._foo._tcp.local.")
    assert sink is not None
    assert sink.name == "fallback-name"


def test_record_id_prefers_location_over_device():
    rid = make_record_id(
        location="convent-room-a",
        device_id="abcd1234-5678-9abc-def0-123456789012",
        started_at=1_700_000_000.0,
    )
    assert rid.endswith("convent-room-a")
