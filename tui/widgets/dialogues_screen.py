"""Dialogues / Topos attach menu — Grant Access via loopback OAuth."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from dialogues.topos_client import ToposClient


class DialoguesAttachPromptScreen(ModalScreen):
    """First-run prompt: attach Dialogues before using Topos ingest."""

    DEFAULT_CSS = """
    DialoguesAttachPromptScreen {
        align: center middle;
    }
    #dialogues-prompt-dialog {
        width: 72;
        height: auto;
        border: heavy #4a90d9;
        border-title-color: #7ec8ff;
        border-title-style: bold;
        background: #0a0e14;
        padding: 1 2;
    }
    #dialogues-prompt-body {
        height: auto;
        color: #c0d8f0;
        margin-bottom: 1;
    }
    #dialogues-prompt-list {
        height: auto;
        max-height: 6;
        background: #111822;
        border: tall #2a3a48;
    }
    #dialogues-prompt-list:focus {
        border: tall #4a90d9;
    }
    #dialogues-prompt-hint {
        height: auto;
        color: #607080;
        margin-top: 1;
    }
    #dialogues-prompt-busy {
        height: auto;
        color: #ff8866;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "decline", "Not now"),
        Binding("n", "decline", "Not now"),
    ]

    def __init__(
        self,
        topos_client: "ToposClient | None",
        *,
        on_attach_complete: Callable[[], None] | None = None,
        on_decline: Callable[[], None] | None = None,
    ):
        super().__init__()
        self._client = topos_client
        self._on_attach_complete = on_attach_complete
        self._on_decline = on_decline
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogues-prompt-dialog") as dialog:
            dialog.border_title = "ATTACH DIALOGUES"
            yield Static(self._body_text(), id="dialogues-prompt-body", markup=True)
            yield OptionList(
                Option("Yes — attach my Dialogues account", id="yes"),
                Option("Not now", id="no"),
                id="dialogues-prompt-list",
            )
            yield Static("", id="dialogues-prompt-busy", markup=True)
            yield Static(
                "[#607080]ENTER[/] to choose   [#607080]N[/] or [#607080]ESC[/] to skip",
                id="dialogues-prompt-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        try:
            self.query_one("#dialogues-prompt-list", OptionList).focus()
        except Exception:
            pass

    def _body_text(self) -> str:
        if self._busy:
            return (
                "[#7ec8ff]Complete login in your browser…[/]\n"
                "[#607080]Waiting for Dialogues to redirect back to VoxTerm.[/]"
            )
        return (
            "[#7ec8ff]Send transcripts to your personal Topos?[/]\n"
            "[#607080]Attach your Dialogues account once. "
            "You can enable push later from the Dialogues menu ([#607080]D[/]).[/]"
        )

    def _refresh(self) -> None:
        try:
            self.query_one("#dialogues-prompt-body", Static).update(self._body_text())
            busy = self.query_one("#dialogues-prompt-busy", Static)
            ol = self.query_one("#dialogues-prompt-list", OptionList)
            if self._busy:
                busy.update("[#ff8866]Browser login in progress…[/]")
                ol.disabled = True
            else:
                busy.update("")
                ol.disabled = False
        except Exception:
            pass

    def action_decline(self) -> None:
        if self._busy:
            return
        if self._on_decline is not None:
            try:
                self._on_decline()
            except Exception:
                pass
        self.dismiss(False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._busy:
            return
        if event.option.id == "no":
            self.action_decline()
            return
        if event.option.id == "yes":
            self._start_attach()

    def _start_attach(self) -> None:
        self._busy = True
        self._refresh()

        def worker() -> None:
            err: Optional[str] = None
            try:
                from dialogues.oauth_loopback import OAuthCallbackError, run_attach_flow

                run_attach_flow()
            except OAuthCallbackError as exc:
                err = str(exc)
            except Exception as exc:
                err = str(exc)

            def finish() -> None:
                self._busy = False
                if err:
                    self._announce(f"dialogues attach failed: {err}")
                    self._refresh()
                else:
                    self._announce("dialogues: attached — press D to enable Topos push")
                    if self._on_attach_complete is not None:
                        try:
                            self._on_attach_complete()
                        except Exception:
                            pass
                    self.dismiss(True)

            try:
                self.app.call_from_thread(finish)
            except Exception:
                finish()

        threading.Thread(target=worker, daemon=True, name="dialogues-attach-prompt").start()

    def _announce(self, msg: str) -> None:
        try:
            self.app.notify(msg, severity="information", timeout=8)
        except Exception:
            pass
        try:
            from tui.widgets.transcript import Log, TranscriptPanel

            tp = self.app.query_one(TranscriptPanel)
            tp.system_message(msg, Log.SYS)
        except Exception:
            pass


class DialoguesScreen(ModalScreen):
    """Attach Dialogues account and toggle Topos transcript push."""

    DEFAULT_CSS = """
    DialoguesScreen {
        align: center middle;
    }
    #dialogues-dialog {
        width: 78;
        height: auto;
        max-height: 24;
        border: heavy #4a90d9;
        border-title-color: #7ec8ff;
        border-title-style: bold;
        background: #0a0e14;
        padding: 1 2;
    }
    #dialogues-status {
        height: auto;
        color: #7ec8ff;
        margin-bottom: 1;
    }
    #dialogues-actions {
        height: auto;
        max-height: 10;
        background: #111822;
        border: tall #2a3a48;
    }
    #dialogues-actions:focus {
        border: tall #4a90d9;
    }
    #dialogues-hint {
        height: auto;
        color: #607080;
        margin-top: 1;
    }
    #dialogues-busy {
        height: auto;
        color: #ff8866;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        topos_client: "ToposClient | None",
        *,
        on_attach_complete: Callable[[], None] | None = None,
        on_detach: Callable[[], None] | None = None,
    ):
        super().__init__()
        self._client = topos_client
        self._on_attach_complete = on_attach_complete
        self._on_detach = on_detach
        self._busy = False
        self._busy_message = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialogues-dialog") as dialog:
            dialog.border_title = "DIALOGUES / TOPOS"
            yield Static(self._status_text(), id="dialogues-status", markup=True)
            yield OptionList(*self._action_options(), id="dialogues-actions")
            yield Static("", id="dialogues-busy", markup=True)
            yield Static(
                " [#607080]ENTER[/] select   "
                "[#607080]R[/] refresh   "
                "[#607080]ESC[/] close",
                id="dialogues-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        try:
            self.query_one("#dialogues-actions", OptionList).focus()
        except Exception:
            pass

    def _status_text(self) -> str:
        if self._client is None:
            return "[#ff8866]Topos client unavailable[/]"
        from dialogues.credentials import load_credentials

        creds = load_credentials()
        if creds is None:
            return (
                "[#7ec8ff]Not attached[/]\n"
                "[#607080]Attach your Dialogues account to send transcripts "
                "to your personal Topos.[/]"
            )
        push = self._client.push_enabled
        rid = creds.resource_id
        short = rid if len(rid) <= 48 else rid[:24] + "…" + rid[-12:]
        if push:
            return (
                f"[#5fa850]pushing to Topos[/]\n"
                f"[#607080]resource: {short}[/]"
            )
        return (
            f"[#7ec8ff]attached[/]  [#607080](push off)[/]\n"
            f"[#607080]resource: {short}[/]"
        )

    def _action_options(self) -> list[Option]:
        from dialogues.credentials import load_credentials

        attached = load_credentials() is not None
        if self._busy:
            return [Option(f"( {self._busy_message} )", id="_busy", disabled=True)]
        opts: list[Option] = []
        if not attached:
            opts.append(Option("Attach Dialogues account (opens browser)", id="attach"))
        else:
            push_on = self._client is not None and self._client.push_enabled
            mark = "[#5fa850][x][/]" if push_on else "[#607080][ ][/]"
            opts.append(Option(f"{mark}  Send transcripts to Topos", id="toggle_push"))
            opts.append(Option("Detach Dialogues account", id="detach"))
        return opts or [Option("(nothing to do)", id="_empty", disabled=True)]

    def _refresh_view(self) -> None:
        try:
            self.query_one("#dialogues-status", Static).update(self._status_text())
            busy = self.query_one("#dialogues-busy", Static)
            busy.update(f"[#ff8866]{self._busy_message}[/]" if self._busy else "")
            ol = self.query_one("#dialogues-actions", OptionList)
            ol.clear_options()
            for opt in self._action_options():
                ol.add_option(opt)
        except Exception:
            pass

    def action_refresh(self) -> None:
        self._refresh_view()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        if opt_id in (None, "_busy", "_empty"):
            return
        if opt_id == "attach":
            self._start_attach()
        elif opt_id == "toggle_push":
            self._toggle_push()
        elif opt_id == "detach":
            self._detach_account()

    def _start_attach(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._busy_message = "Waiting for browser login…"
        self._refresh_view()

        def worker() -> None:
            err: Optional[str] = None
            try:
                from dialogues.oauth_loopback import OAuthCallbackError, run_attach_flow

                run_attach_flow()
            except OAuthCallbackError as exc:
                err = str(exc)
            except Exception as exc:
                err = str(exc)

            def finish() -> None:
                self._busy = False
                self._busy_message = ""
                if err:
                    self._announce(f"dialogues attach failed: {err}")
                else:
                    self._announce("dialogues: attached — enable push to send transcripts")
                    if self._on_attach_complete is not None:
                        try:
                            self._on_attach_complete()
                        except Exception:
                            pass
                self._refresh_view()

            try:
                self.app.call_from_thread(finish)
            except Exception:
                finish()

        threading.Thread(target=worker, daemon=True, name="dialogues-attach").start()

    def _toggle_push(self) -> None:
        if self._client is None:
            return
        if self._client.push_enabled:
            self._client.disable_push()
            self._announce("dialogues: push disabled")
        else:
            self._client.enable_push()
            self._announce("dialogues: push enabled")
        self._refresh_view()

    def _detach_account(self) -> None:
        """Remove stored Dialogues credentials (not Textual DOM _detach)."""
        from dialogues.credentials import clear_credentials

        if self._client is not None:
            self._client.disable_push()
        clear_credentials()
        if self._on_detach is not None:
            try:
                self._on_detach()
            except Exception:
                pass
        self._announce("dialogues: detached")
        self._refresh_view()

    def _announce(self, msg: str) -> None:
        try:
            self.app.notify(msg, severity="information", timeout=6)
        except Exception:
            pass
        try:
            from tui.widgets.transcript import Log, TranscriptPanel

            tp = self.app.query_one(TranscriptPanel)
            tp.system_message(msg, Log.SYS)
        except Exception:
            pass
