"""Tests for ToposClient batch expansion and posting."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from dialogues.credentials import DialoguesCredentials, save_credentials, clear_credentials
from dialogues.topos_client import ToposClient, expand_batch_to_records


@pytest.fixture(autouse=True)
def _clean_creds(tmp_path, monkeypatch):
    monkeypatch.setattr("dialogues.credentials.DATA_DIR", tmp_path)
    clear_credentials()
    yield
    clear_credentials()


def test_expand_batch_to_records():
    payload = {
        "record_id": "transcript-2026-06-24-1430-voxterm",
        "batch_index": 0,
        "started_at": "2026-06-24T14:30:00Z",
        "ended_at": "2026-06-24T14:30:05Z",
        "origin_device": "dev-1",
        "segments": [
            {"t": 0.0, "speaker": "Alice", "text": "Hello"},
            {"t": 2.5, "speaker": "Bob", "text": "Hi there"},
        ],
    }
    rows = expand_batch_to_records(payload, origin_device="dev-1", location="desk")
    assert len(rows) == 2
    assert rows[0]["message_id"] == "transcript-2026-06-24-1430-voxterm:0:0"
    assert rows[0]["conversation_id"] == "transcript-2026-06-24-1430-voxterm"
    assert rows[0]["sender_id"] == "Alice"
    assert rows[0]["content"] == "Hello"
    assert rows[0]["event_at"] == "2026-06-24T14:30:00Z"
    assert rows[1]["event_at"] == "2026-06-24T14:30:02Z"
    assert rows[0]["location"] == "desk"


def test_topos_client_flush_posts_when_enabled():
    save_credentials(
        plugin_attach_token="tok-test",
        resource_id="dataset:user:default:device",
        cp_url="https://cp.example.com",
    )
    poster = MagicMock()
    posted: list[tuple[int, int, str]] = []
    client = ToposClient(
        push_enabled=True,
        device_id="dev-1",
        poster=poster,
        on_batch_posted=lambda batch, count, record_id: posted.append((batch, count, record_id)),
        clock=lambda: 0.0,
        wall_clock=lambda: __import__("datetime").datetime(
            2026, 6, 24, 14, 30, 0, tzinfo=__import__("datetime").timezone.utc
        ),
    )
    client.add_segment(0.0, "Alice", "Hello")
    assert client.flush_now() is True
    poster.assert_called_once()
    creds, records = poster.call_args[0]
    assert isinstance(creds, DialoguesCredentials)
    assert len(records) == 1
    assert records[0]["content"] == "Hello"
    assert posted == [(0, 1, client.record_id)]


def test_topos_client_drops_when_push_disabled():
    save_credentials(
        plugin_attach_token="tok-test",
        resource_id="dataset:user:default:device",
    )
    poster = MagicMock()
    client = ToposClient(push_enabled=False, poster=poster)
    client.add_segment(0.0, "Alice", "Hello")
    assert client.flush_now() is False
    poster.assert_not_called()
