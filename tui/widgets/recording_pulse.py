from __future__ import annotations

from typing import Optional

from textual.timer import Timer


class RecordingPulse:
    """Drives the full-viewport pulsing red border by toggling CSS classes.

    Textual TCSS has no keyframe animations, so we flip a class on a fixed
    cadence and let the screen's `transition: border ...` smooth the fade.
    """

    INTERVAL_SEC = 0.6

    def __init__(self, screen) -> None:
        self._screen = screen
        self._timer: Optional[Timer] = None
        self._dim = False

    def start(self) -> None:
        if self._timer is not None:
            return
        self._screen.add_class("--recording")
        self._dim = False
        self._timer = self._screen.set_interval(self.INTERVAL_SEC, self._tick)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._screen.remove_class("--recording")
        self._screen.remove_class("--recording-pulse")
        self._dim = False

    def _tick(self) -> None:
        self._dim = not self._dim
        if self._dim:
            self._screen.add_class("--recording-pulse")
        else:
            self._screen.remove_class("--recording-pulse")
