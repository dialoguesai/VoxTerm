import time
from textual.widget import Widget
from textual.strip import Strip
from rich.text import Text
from rich.style import Style


class CyberHeader(Widget):
    """Header that transforms into a recording indicator when active."""

    DEFAULT_CSS = """
    CyberHeader {
        height: 1;
        background: #0a0e14;
        layer: base;
    }
    """

    def __init__(self):
        super().__init__()
        self._recording = False
        self._rec_start: float = 0.0
        self._hivemind_active = False
        self._hivemind_name = ""

    def set_recording(self, on: bool):
        self._recording = on
        if on:
            self._rec_start = time.time()
        self.refresh()

    def set_hivemind(self, active: bool, name: str = "") -> None:
        """Toggle the hivemind status badge. Visible in both idle and
        recording states so the user always knows when transcripts are
        being published."""
        self._hivemind_active = active
        self._hivemind_name = name or ""
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y != 0:
            return Strip.blank(width)

        if self._recording:
            elapsed = int(time.time() - self._rec_start)
            mins, secs = divmod(elapsed, 60)
            ts = f"{mins:02d}:{secs:02d}"

            line = Text()
            rec_style = Style(color="#ffffff", bgcolor="#cc0000", bold=True)
            bar_style = Style(color="#cc0000", bgcolor="#cc0000")
            line.append(f"  ● REC {ts} ", rec_style)
            if self._hivemind_active:
                # White-on-red continues the bar, then the hivemind chip
                # pops in cyan-on-dark so it's unmistakable while live.
                hive_style = Style(color="#0a0e14", bgcolor="#00ffcc", bold=True)
                bar_style_continue = Style(color="#cc0000", bgcolor="#cc0000")
                line.append(" ", bar_style_continue)
                line.append(" ⬢ PUBLISHING ", hive_style)
            # Fill the rest with the red bar
            remaining = max(0, width - line.cell_len)
            line.append("━" * remaining, bar_style)
            line.truncate(width)
            return Strip(line.render(self.app.console))
        else:
            line = Text()
            line.append("  +++ ", Style(color="#00e5ff", bold=True))
            from config import VERSION
            line.append(f"VOXTERM v{VERSION}", Style(color="#00ffcc", bold=True))
            line.append(" // ", Style(color="#607080"))
            line.append("LOCAL VOICE TRANSCRIPTION ENGINE", Style(color="#00e5ff", bold=True))
            if self._hivemind_active:
                line.append("  ", Style())
                line.append(
                    f"⬢ HIVEMIND",
                    Style(color="#00ff88", bold=True),
                )
                if self._hivemind_name:
                    line.append(
                        f" · {self._hivemind_name}",
                        Style(color="#607080"),
                    )
            line.pad(width)
            return Strip(line.render(self.app.console))
