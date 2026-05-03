from __future__ import annotations

from textual.app import App
from textual.timer import Timer


class RecordingPulse:
    """Drives the full-viewport pulsing red border by toggling CSS classes.

    Textual TCSS has no keyframe animations, so we flip a class on a fixed
    cadence and let the screen's `transition: border ...` smooth the fade.

    Targets every screen in the App's stack (not just the screen present at
    construction time) so the indicator stays visible when modal screens
    push on top of the main screen.
    """

    INTERVAL_SEC = 0.6

    def __init__(self, app: App) -> None:
        self._app = app
        self._timer: Timer | None = None
        self._dim = False
        self._on = False

    def start(self) -> None:
        if self._timer is not None:
            return
        self._on = True
        self._dim = False
        self._reapply()
        self._timer = self._app.set_interval(self.INTERVAL_SEC, self._tick)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._on = False
        self._dim = False
        for screen in self._screens():
            screen.remove_class("--recording")
            screen.remove_class("--recording-pulse")

    def reapply_to_active_screen(self) -> None:
        """Call when a new screen is pushed/resumed, so the indicator
        carries over to it without waiting for the next tick."""
        if self._on:
            self._reapply()

    def _tick(self) -> None:
        if not self._on:
            return
        self._dim = not self._dim
        self._reapply()

    def _reapply(self) -> None:
        for screen in self._screens():
            screen.add_class("--recording")
            if self._dim:
                screen.add_class("--recording-pulse")
            else:
                screen.remove_class("--recording-pulse")

    def _screens(self):
        try:
            return list(self._app.screen_stack)
        except Exception:
            return [self._app.screen]
