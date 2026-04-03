"""Network bar — one-line P2P group browser and info display.

Press N to open. Shows available groups (browse mode) or current group
info (info mode).  Non-blocking — recording and transcription continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from textual.message import Message
from textual.widgets import Static


class NetworkBar(Static):
    """One-line network bar for P2P group management.

    Two modes:
    - **browse**: not in a group — shows nearby groups, [C]reate, arrow+ENTER to join
    - **info**: in a group — shows peers + latency, [L]eave
    """

    DEFAULT_CSS = """
    NetworkBar {
        dock: bottom;
        width: 100%;
        height: 1;
        background: #0d1520;
        color: #c0c0c0;
        border-top: heavy #00e5ff;
        layer: notifications;
    }
    """

    # ── messages ──────────────────────────────────────────────

    class Create(Message):
        """User pressed C to create a group."""

    class Join(Message):
        """User pressed ENTER to join a group."""
        def __init__(self, group_name: str, peer_ip: str, peer_port: int) -> None:
            super().__init__()
            self.group_name = group_name
            self.peer_ip = peer_ip
            self.peer_port = peer_port

    class Leave(Message):
        """User pressed L to leave the group."""

    class Dismissed(Message):
        """Bar was dismissed (ESC or N toggle)."""

    # ── dataclass for group info ──────────────────────────────

    @dataclass
    class GroupInfo:
        name: str
        peer_count: int
        creator_ip: str
        creator_port: int

    # ── init ──────────────────────────────────────────────────

    def __init__(
        self,
        mode: str,  # "browse" or "info"
        groups: list[GroupInfo] | None = None,
        connected_peers: list[tuple[str, float]] | None = None,  # (name, rtt_ms)
        group_name: str = "",
    ) -> None:
        super().__init__()
        self._mode = mode
        self._groups = groups or []
        self._connected_peers = connected_peers or []
        self._group_name = group_name
        self._selected = 0  # index into _groups for browse mode

    def render(self) -> str:
        if self._mode == "info":
            return self._render_info()
        return self._render_browse()

    def _render_browse(self) -> str:
        parts = []
        if not self._groups:
            parts.append("[dim]No groups nearby[/]")
        else:
            for i, g in enumerate(self._groups):
                if i == self._selected:
                    parts.append(f"[b #00ffcc]▸ {g.name}'s group ({g.peer_count})[/]")
                else:
                    parts.append(f"[dim]{g.name}'s group ({g.peer_count})[/]")

        parts.append("[b #00e5ff]\\[C][/]reate")
        if self._groups:
            parts.append("[dim]←→ ENTER join[/]")
        parts.append("[dim]ESC[/]")
        return "  " + "  ·  ".join(parts)

    def _render_info(self) -> str:
        parts = [f"[b #00e5ff]GROUP[/]"]
        if self._connected_peers:
            for name, rtt in self._connected_peers:
                lat = f" {rtt:.0f}ms" if rtt >= 0 else ""
                parts.append(f"[#00ffcc]●[/] {name}{lat}")
        else:
            parts.append(f"[dim]{self._group_name} (waiting...)[/]")
        parts.append("[b #00e5ff]\\[L][/]eave")
        parts.append("[dim]ESC[/]")
        return "  " + "  ·  ".join(parts)

    def handle_key(self, key: str) -> bool:
        """Handle a keypress. Returns True if consumed."""
        if key == "escape" or key == "n":
            self.post_message(self.Dismissed())
            return True

        if self._mode == "browse":
            if key == "c":
                self.post_message(self.Create())
                return True
            if key == "enter" and self._groups:
                g = self._groups[self._selected]
                self.post_message(self.Join(g.name, g.creator_ip, g.creator_port))
                return True
            if key == "left" and self._groups:
                self._selected = (self._selected - 1) % len(self._groups)
                self.refresh()
                return True
            if key == "right" and self._groups:
                self._selected = (self._selected + 1) % len(self._groups)
                self.refresh()
                return True

        elif self._mode == "info":
            if key == "l":
                self.post_message(self.Leave())
                return True

        return False
