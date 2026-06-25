"""Tests for Dialogues OAuth loopback callback capture."""

from __future__ import annotations

import threading
import urllib.parse
import urllib.request

from dialogues.oauth_loopback import _wait_for_callback


def test_wait_for_callback_receives_code():
    expected_state = "grant-test-state"
    result: dict[str, str] = {}

    def requester():
        import time
        time.sleep(0.2)
        qs = urllib.parse.urlencode({"code": "auth-code-123", "state": expected_state})
        urllib.request.urlopen(f"http://127.0.0.1:8741/oauth/callback?{qs}", timeout=5)

    t = threading.Thread(target=requester, daemon=True)
    t.start()
    code, state = _wait_for_callback(expected_state=expected_state, timeout=5.0, port=8741)
    result["code"] = code
    result["state"] = state
    assert code == "auth-code-123"
    assert state == expected_state
