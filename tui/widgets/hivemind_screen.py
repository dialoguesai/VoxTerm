"""Hivemind setup modal — discover swf-node transcript sinks on the LAN.

Shows a pulsing-radar animation while mDNS-browsing for service
`_shape-rotator-hivemind._tcp.local.`. Discovered sinks fill the list as
they arrive. ENTER picks one; D disables hivemind; ESC cancels.

Returns one of:
    {"action": "select", "sink": Sink}    — user picked a sink
    {"action": "disable"}                  — user disabled hivemind
    None                                   — cancelled
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from rich.style import Style
from rich.text import Text

from network.hivemind import HivemindBrowser, Sink


class _RadarPulse(Widget):
    """Pulsing concentric-rings animation. Cycles frames at ~6 fps to
    show that mDNS is actively scanning. Five frames; the centre hex
    stays put while rings radiate outward and fade."""

    DEFAULT_CSS = """
    _RadarPulse {
        height: 5;
        width: 100%;
        content-align: center middle;
        background: #0a0e14;
    }
    """

    # Each frame is a list of 5 lines. Spaces are intentional — they
    # frame the hex.
    _FRAMES: list[list[str]] = [
        [
            "                  ",
            "                  ",
            "        ⬢         ",
            "                  ",
            "                  ",
        ],
        [
            "                  ",
            "       · · ·      ",
            "      ·  ⬢  ·     ",
            "       · · ·      ",
            "                  ",
        ],
        [
            "      · · · · ·   ",
            "     ·         ·  ",
            "    ·     ⬢     · ",
            "     ·         ·  ",
            "      · · · · ·   ",
        ],
        [
            "    · · · · · · · ",
            "   ·             ·",
            "  ·       ⬢       ·",
            "   ·             ·",
            "    · · · · · · · ",
        ],
        [
            "                  ",
            "                  ",
            "        ⬢         ",
            "                  ",
            "                  ",
        ],
    ]

    def __init__(self) -> None:
        super().__init__()
        self._frame = 0

    def on_mount(self) -> None:
        self.set_interval(1.0 / 6.0, self._advance)

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self.refresh()

    def render(self) -> Text:
        # Brighter rings on the outer pulse, dimmer on the centre — like
        # a sonar ping fading as it expands.
        lines = self._FRAMES[self._frame]
        style_outer = Style(color="#00e5ff", bold=True)
        style_inner = Style(color="#00ffcc", bold=True)
        out = Text()
        for line in lines:
            t = Text()
            for ch in line:
                if ch == "⬢":
                    t.append(ch, Style(color="#00ff88", bold=True))
                else:
                    t.append(ch, style_outer if self._frame >= 2 else style_inner)
            out.append(t)
            out.append("\n")
        return out


class HivemindScreen(ModalScreen):
    """Sink picker modal — drives a HivemindBrowser, lists results live."""

    DEFAULT_CSS = """
    HivemindScreen {
        align: center middle;
    }
    #hive-dialog {
        width: 60;
        height: auto;
        max-height: 26;
        border: heavy #00e5ff;
        border-title-color: #00ffcc;
        border-title-style: bold;
        background: #0a0e14;
        padding: 1 2;
    }
    #hive-status {
        height: 1;
        color: #00ffcc;
        margin-bottom: 1;
    }
    #hive-section-label {
        height: 1;
        color: #607080;
        margin-top: 1;
    }
    #hive-list {
        height: auto;
        max-height: 8;
        background: #0a0e14;
        color: #c0c0c0;
    }
    #hive-list > .option-list--option-highlighted {
        background: #1a1a3a;
        color: #00ffcc;
    }
    #hive-empty {
        height: 1;
        color: #607080;
        text-style: italic;
    }
    #hive-current {
        height: 1;
        color: #ffaa00;
        margin-top: 1;
    }
    #hive-hint {
        height: 1;
        color: #607080;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("d", "disable", "Disable", show=False),
    ]

    def __init__(self, current_pubkey: str = "", current_name: str = "") -> None:
        super().__init__()
        self._current_pubkey = current_pubkey
        self._current_name = current_name
        self._sinks: list[Sink] = []
        self._browser: HivemindBrowser | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="hive-dialog") as dialog:
            dialog.border_title = "HIVEMIND // PROBE"

            yield Static(
                "  [#00ffcc]scanning network...[/]",
                id="hive-status",
                markup=True,
            )
            yield _RadarPulse()
            yield Static(
                "DISCOVERED SINKS",
                id="hive-section-label",
            )
            yield OptionList(id="hive-list")
            yield Static(
                "  [italic #607080]none yet — keep this open to discover[/]",
                id="hive-empty",
                markup=True,
            )
            if self._current_pubkey:
                short = self._current_pubkey[:8]
                yield Static(
                    f"  current: [#ffaa00]{self._current_name}[/] ({short}…)",
                    id="hive-current",
                    markup=True,
                )
            yield Static(
                " [#607080]ENTER[/] connect  "
                "[#607080]D[/] disable  "
                "[#607080]ESC[/] cancel",
                id="hive-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        # Fire up the browser. The callback fires from the zeroconf
        # thread, so we marshal onto the Textual loop.
        self._browser = HivemindBrowser(on_change=self._on_sinks_changed)
        self._browser.start()

    def on_unmount(self) -> None:
        if self._browser is not None:
            self._browser.stop()
            self._browser = None

    # ── browser callback (runs on zeroconf thread) ────────────────────

    def _on_sinks_changed(self, sinks: list[Sink]) -> None:
        self.app.call_from_thread(self._update_list, sinks)

    # ── UI updates (Textual thread) ───────────────────────────────────

    def _update_list(self, sinks: list[Sink]) -> None:
        self._sinks = sinks
        ol = self.query_one("#hive-list", OptionList)
        ol.clear_options()

        empty_widget = self.query_one("#hive-empty", Static)
        if not sinks:
            empty_widget.display = True
            return
        empty_widget.display = False

        for idx, s in enumerate(sinks):
            label = f"  {s.display}"
            if s.pubkey and s.pubkey == self._current_pubkey:
                label = f"  ★ {s.display}  [current]"
            ol.add_option(Option(label, id=str(idx)))

        # Update status line — "scanning network..." → "N found"
        status = self.query_one("#hive-status", Static)
        n = len(sinks)
        plural = "s" if n != 1 else ""
        status.update(f"  [#00ff88]{n} sink{plural} found · still listening...[/]")

    # ── actions ───────────────────────────────────────────────────────

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        if not event.option or event.option.id is None:
            return
        try:
            idx = int(event.option.id)
        except ValueError:
            return
        if 0 <= idx < len(self._sinks):
            self.dismiss({"action": "select", "sink": self._sinks[idx]})

    def action_disable(self) -> None:
        if self._current_pubkey:
            self.dismiss({"action": "disable"})

    def action_cancel(self) -> None:
        self.dismiss(None)
