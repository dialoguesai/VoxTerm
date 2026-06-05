"""Tests for the GUI capture-source selection (mic / system / both) and chunk mixing."""

import numpy as np

from gui.engine import Engine, _mix_chunks


# ── _mix_chunks: time-aligned add + tails (mirrors tui.app.VoxTerm._mix_chunks) ──

def test_mix_chunks_sums_overlap_and_clips():
    mic = [np.array([0.5, 0.5], dtype=np.float32)]
    sysa = [np.array([0.6, -0.6], dtype=np.float32)]
    out = _mix_chunks(mic, sysa)
    assert len(out) == 1
    # 0.5+0.6 = 1.1 -> clipped to 1.0 ; 0.5-0.6 = -0.1 -> unchanged
    np.testing.assert_allclose(out[0], [1.0, -0.1], atol=1e-6)


def test_mix_chunks_keeps_mic_tail():
    mic = [np.array([0.1], dtype=np.float32), np.array([0.2], dtype=np.float32)]
    sysa = [np.array([0.1], dtype=np.float32)]
    out = _mix_chunks(mic, sysa)
    assert len(out) == 2
    np.testing.assert_allclose(out[0], [0.2], atol=1e-6)   # summed overlap
    np.testing.assert_allclose(out[1], [0.2], atol=1e-6)   # mic-only tail preserved


def test_mix_chunks_keeps_system_tail():
    mic = [np.array([0.1], dtype=np.float32)]
    sysa = [np.array([0.1], dtype=np.float32), np.array([0.3], dtype=np.float32)]
    out = _mix_chunks(mic, sysa)
    assert len(out) == 2
    np.testing.assert_allclose(out[1], [0.3], atol=1e-6)   # system-only tail preserved


def test_drain_none_is_empty():
    assert Engine._drain(None) == []


# ── source selection wiring (capture classes mocked — no hardware needed) ──

class _FakeCap:
    def __init__(self, active=True):
        self._active = active
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def drain(self):
        return []

    @property
    def is_active(self):
        return self._active

    @property
    def status_message(self):
        return "" if self._active else "no monitor source"


def _patch(monkeypatch, mic_active=True, sys_active=True):
    import audio.capture
    import audio.system_capture
    mic, sysa = _FakeCap(mic_active), _FakeCap(sys_active)
    monkeypatch.setattr(audio.capture, "AudioCapture", lambda: mic)
    monkeypatch.setattr(audio.system_capture, "SystemCapture", lambda: sysa)
    return mic, sysa


def test_source_system_uses_only_system_capture(monkeypatch, tmp_path):
    mic, sysa = _patch(monkeypatch)
    e = Engine(out_dir=tmp_path)
    r = e.start_recording(source="system")
    try:
        assert r["ok"], r
        assert e._cap is None and e._sys is sysa and sysa.started
        assert e._source == "system"
    finally:
        e.stop_recording(model="fw-base")


def test_source_both_uses_both_captures(monkeypatch, tmp_path):
    mic, sysa = _patch(monkeypatch)
    e = Engine(out_dir=tmp_path)
    r = e.start_recording(source="both")
    try:
        assert r["ok"], r
        assert e._cap is mic and e._sys is sysa and mic.started and sysa.started
    finally:
        e.stop_recording(model="fw-base")


def test_invalid_source_falls_back_to_mic(monkeypatch, tmp_path):
    mic, sysa = _patch(monkeypatch)
    e = Engine(out_dir=tmp_path)
    r = e.start_recording(source="bogus")
    try:
        assert r["ok"], r
        assert e._source == "mic" and e._cap is mic and e._sys is None
    finally:
        e.stop_recording(model="fw-base")


def test_system_unavailable_fails_gracefully(monkeypatch, tmp_path):
    mic, sysa = _patch(monkeypatch, sys_active=False)
    e = Engine(out_dir=tmp_path)
    r = e.start_recording(source="system")
    assert not r["ok"]
    assert "system audio" in r["error"]
    assert e.recording is False and e._sys is None
