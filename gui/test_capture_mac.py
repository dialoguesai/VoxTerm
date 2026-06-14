"""Tests for audio/capture.py's macOS mic-permission branches (added for mac compat).

On macOS, sounddevice raises (or reports 0 input channels) when TCC hasn't granted
Microphone permission. capture.start() turns that into an actionable RuntimeError instead
of an opaque failure. These tests monkeypatch sounddevice + sys.platform so they run on any
host (no real mic needed). Pytest-style; also runnable standalone.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (str(_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import audio.capture as cap


class _Mp:
    """Tiny monkeypatch helper (so this also runs standalone, without pytest)."""
    def __init__(self):
        self._undo = []
    def setattr(self, obj, name, val):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)
    def undo(self):
        for obj, name, val in reversed(self._undo):
            setattr(obj, name, val)


def test_mac_mic_query_error_is_actionable():
    mp = _Mp()
    mp.setattr(cap.sys, "platform", "darwin")
    def boom(*a, **k):
        raise RuntimeError("PortAudioError: no default input device")
    mp.setattr(cap.sd, "query_devices", boom)
    try:
        c = cap.AudioCapture()
        raised = None
        try:
            c.start()
        except RuntimeError as e:
            raised = str(e)
        assert raised is not None, "start() should raise on a mic query error"
        assert "Microphone permission" in raised, raised
    finally:
        mp.undo()


def test_mac_zero_channels_is_actionable():
    mp = _Mp()
    mp.setattr(cap.sys, "platform", "darwin")
    mp.setattr(cap.sd, "query_devices",
               lambda *a, **k: {"name": "x", "max_input_channels": 0, "default_samplerate": 48000})
    try:
        c = cap.AudioCapture()
        raised = None
        try:
            c.start()
        except RuntimeError as e:
            raised = str(e)
        assert raised is not None, "start() should raise when the device reports 0 channels"
        assert ("0 channels" in raised) or ("Microphone permission" in raised), raised
    finally:
        mp.undo()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
