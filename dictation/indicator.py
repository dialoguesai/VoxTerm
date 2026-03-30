"""Status indicator for dictation mode.

macOS: rumps menu bar app (NSStatusBar).
Linux: pystray system tray icon.
Fallback: stdout logging only.
"""

from __future__ import annotations

import logging
import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable

from audio.platform import CURRENT_PLATFORM, Platform

log = logging.getLogger(__name__)


class DictationIndicator(ABC):
    """Base class for platform-specific status indicators."""

    def __init__(
        self,
        on_quit: Callable[[], None] | None = None,
        model_name: str = "",
        language: str = "",
    ):
        self._on_quit = on_quit or (lambda: None)
        self._model_name = model_name
        self._language = language
        self._state = "idle"

    @abstractmethod
    def set_state(self, state: str) -> None:
        """Update indicator: 'idle', 'listening', 'transcribing'."""

    @abstractmethod
    def run(self) -> None:
        """Start the indicator (may block the main thread)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the indicator."""


_STATE_TITLES = {
    "idle": "VOXTERM [idle]",
    "listening": "VOXTERM [listening]",
    "transcribing": "VOXTERM [transcribing]",
    "loading": "VOXTERM [loading...]",
}


# ---------------------------------------------------------------------------
# macOS: rumps menu bar app
# ---------------------------------------------------------------------------

class _RumpsIndicator(DictationIndicator):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._app = None

    def set_state(self, state: str) -> None:
        self._state = state
        if self._app is not None:
            self._app.title = _STATE_TITLES.get(state, f"VOXTERM [{state}]")

    def run(self) -> None:
        try:
            import rumps
        except ImportError:
            log.warning("rumps not installed — falling back to stdout indicator")
            _StdoutIndicator(
                on_quit=self._on_quit,
                model_name=self._model_name,
                language=self._language,
            ).run()
            return

        class _App(rumps.App):
            pass

        self._app = _App(
            _STATE_TITLES.get(self._state, "VOXTERM"),
            icon=None,
            quit_button=None,
        )

        self._app.menu = [
            rumps.MenuItem(f"Model: {self._model_name}"),
            rumps.MenuItem(f"Language: {self._language}"),
            None,  # separator
            rumps.MenuItem("Quit", callback=lambda _: self._quit()),
        ]

        self._app.run()

    def stop(self) -> None:
        if self._app is not None:
            try:
                import rumps
                rumps.quit_application()
            except Exception:
                pass

    def _quit(self) -> None:
        self._on_quit()
        self.stop()


# ---------------------------------------------------------------------------
# Linux: pystray system tray
# ---------------------------------------------------------------------------

class _PystrayIndicator(DictationIndicator):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._icon = None
        self._stop_event = threading.Event()

    def set_state(self, state: str) -> None:
        self._state = state
        if self._icon is not None:
            self._icon.title = _STATE_TITLES.get(state, f"VOXTERM [{state}]")
            try:
                self._icon.icon = self._make_icon(state)
            except Exception:
                pass

    def run(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            log.warning("pystray/Pillow not installed — falling back to stdout indicator")
            _StdoutIndicator(
                on_quit=self._on_quit,
                model_name=self._model_name,
                language=self._language,
            ).run()
            return

        icon = pystray.Icon(
            "voxterm",
            icon=self._make_icon(self._state),
            title=_STATE_TITLES.get(self._state, "VOXTERM"),
            menu=pystray.Menu(
                pystray.MenuItem(f"Model: {self._model_name}", None, enabled=False),
                pystray.MenuItem(f"Language: {self._language}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: self._quit()),
            ),
        )
        self._icon = icon
        icon.run()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._stop_event.set()

    def _quit(self) -> None:
        self._on_quit()
        self.stop()

    @staticmethod
    def _make_icon(state: str):
        """Generate a simple colored circle icon."""
        from PIL import Image, ImageDraw
        colors = {"idle": "#808080", "listening": "#00ff88", "transcribing": "#ffcc00"}
        color = colors.get(state, "#808080")
        img = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([2, 2, 20, 20], fill=color)
        return img


# ---------------------------------------------------------------------------
# Fallback: stdout only
# ---------------------------------------------------------------------------

class _StdoutIndicator(DictationIndicator):

    def set_state(self, state: str) -> None:
        old = self._state
        self._state = state
        if state != old:
            print(f"[voxterm] {_STATE_TITLES.get(state, state)}", file=sys.stderr)

    def run(self) -> None:
        print(f"[voxterm] dictation ready (model={self._model_name} lang={self._language})",
              file=sys.stderr)
        self._stop_event = threading.Event()
        self._stop_event.wait()

    def stop(self) -> None:
        if hasattr(self, "_stop_event"):
            self._stop_event.set()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_indicator(**kwargs) -> DictationIndicator:
    """Return the appropriate DictationIndicator for the current platform."""
    if CURRENT_PLATFORM == Platform.MACOS:
        return _RumpsIndicator(**kwargs)
    if CURRENT_PLATFORM == Platform.LINUX:
        return _PystrayIndicator(**kwargs)
    return _StdoutIndicator(**kwargs)
