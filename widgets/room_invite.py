"""Non-blocking room invite toast — slides up when a peer is nearby."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Static


class RoomInviteToast(Static):
    """Non-blocking toast notification for P2P peer discovery.

    Appears at the bottom of the TUI when a VoxTerm peer is found on
    the LAN.  Does not block recording or transcription.  Press ENTER
    to accept (handled by the app's on_key), ESC to dismiss, or let
    it auto-dismiss after the timeout.
    """

    DEFAULT_CSS = """
    RoomInviteToast {
        dock: bottom;
        width: 60;
        height: auto;
        margin: 1 2;
        padding: 1 2;
        background: #0d1520;
        border: heavy #00e5ff;
        border-title-color: #00ffcc;
        border-title-style: bold;
        layer: notifications;
        offset-y: 5;
        opacity: 0;
        transition: offset 400ms out_cubic, opacity 300ms linear;
    }

    RoomInviteToast.--visible {
        offset-y: 0;
        opacity: 1;
    }

    RoomInviteToast.--dismissing {
        offset-y: 5;
        opacity: 0;
    }
    """

    class Accepted(Message):
        """User accepted the room invite."""

        def __init__(self, peer_node_id: str, peer_name: str, peer_ip: str, peer_port: int) -> None:
            super().__init__()
            self.peer_node_id = peer_node_id
            self.peer_name = peer_name
            self.peer_ip = peer_ip
            self.peer_port = peer_port

    class Dismissed(Message):
        """Invite was dismissed (timeout or manual)."""

    def __init__(
        self,
        peer_name: str,
        peer_node_id: str,
        peer_ip: str,
        peer_port: int,
        timeout: float = 10.0,
    ) -> None:
        super().__init__()
        self._peer_name = peer_name
        self._peer_node_id = peer_node_id
        self._peer_ip = peer_ip
        self._peer_port = peer_port
        self._timeout = timeout
        self._timer = None
        self._accepted = False

    def render(self) -> str:
        return (
            f"[b #00e5ff]{self._peer_name}[/] is nearby\n"
            f"\n"
            f"[dim]ENTER to share transcripts  ·  ESC to dismiss[/]"
        )

    def on_mount(self) -> None:
        # Slide in on next frame
        self.set_timer(0.05, self._show)
        # Auto-dismiss after timeout
        self._timer = self.set_timer(self._timeout, self.dismiss_toast)

    def _show(self) -> None:
        self.add_class("--visible")

    def accept(self) -> None:
        """Called when user presses ENTER."""
        if self._accepted:
            return
        self._accepted = True
        if self._timer:
            self._timer.stop()
        self.post_message(self.Accepted(
            self._peer_node_id, self._peer_name,
            self._peer_ip, self._peer_port,
        ))
        self._animate_out()

    def dismiss_toast(self) -> None:
        """Dismiss without accepting."""
        if self._accepted:
            return
        if self._timer:
            self._timer.stop()
        self.post_message(self.Dismissed())
        self._animate_out()

    def _animate_out(self) -> None:
        self.remove_class("--visible")
        self.add_class("--dismissing")
        self.set_timer(0.5, self._remove_self)

    def _remove_self(self) -> None:
        try:
            self.remove()
        except Exception:
            pass
