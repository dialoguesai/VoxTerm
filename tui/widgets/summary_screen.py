"""Summary template picker modal — choose a prompt before save-with-summary."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, OptionList, Input
from textual.widgets.option_list import Option
from textual.binding import Binding
from textual.screen import ModalScreen

from summarizer.prompts import TEMPLATES


class SummaryScreen(ModalScreen):
    """Pick a summarization template and (optionally) supply a custom prompt.

    Dismisses with a dict or None:
        {"template_id": str, "custom_prompt": str}  — proceed
        None                                          — cancelled
    """

    DEFAULT_CSS = """
    SummaryScreen {
        align: center middle;
    }
    #summary-dialog {
        width: 64;
        height: auto;
        max-height: 24;
        border: heavy #00e5ff;
        border-title-color: #00ffcc;
        border-title-style: bold;
        background: #0a0e14;
        padding: 1 2;
    }
    #summary-list {
        height: auto;
        max-height: 10;
        background: #0a0e14;
        color: #c0c0c0;
    }
    #summary-list > .option-list--option-highlighted {
        background: #1a1a3a;
        color: #00ffcc;
    }
    #summary-custom-container {
        height: 3;
        margin-top: 1;
        display: none;
    }
    #summary-custom-container.-visible {
        display: block;
    }
    #summary-custom {
        width: 100%;
        background: #111822;
        color: #00ffcc;
        border: tall #003344;
    }
    #summary-custom:focus {
        border: tall #00e5ff;
    }
    #summary-hint {
        height: 1;
        color: #607080;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        default_template_id: str = "tldr",
        default_custom_prompt: str = "",
    ):
        super().__init__()
        self._default_id = default_template_id
        self._default_custom = default_custom_prompt
        self._selected_id: str = default_template_id

    def compose(self) -> ComposeResult:
        with Vertical(id="summary-dialog") as dialog:
            dialog.border_title = "SAVE WITH SUMMARY"

            options = []
            initial_index = 0
            for idx, tmpl in enumerate(TEMPLATES):
                label = f"  {tmpl.label:18s}  [#607080]{tmpl.description}[/]"
                options.append(Option(label, id=tmpl.id))
                if tmpl.id == self._default_id:
                    initial_index = idx
            ol = OptionList(*options, id="summary-list")
            ol.highlighted = initial_index
            yield ol

            with Vertical(id="summary-custom-container"):
                yield Input(
                    placeholder="Your summarization instruction…",
                    id="summary-custom",
                    value=self._default_custom,
                )

            yield Static(
                " [#607080]ENTER[/] summarize  [#607080]ESC[/] cancel",
                id="summary-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self._sync_custom_visibility()

    def _sync_custom_visibility(self) -> None:
        container = self.query_one("#summary-custom-container")
        if self._selected_id == "custom":
            container.add_class("-visible")
            self.query_one("#summary-custom", Input).focus()
        else:
            container.remove_class("-visible")

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option and event.option.id:
            self._selected_id = event.option.id
            self._sync_custom_visibility()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option and event.option.id:
            self._selected_id = event.option.id
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        custom = ""
        if self._selected_id == "custom":
            custom = self.query_one("#summary-custom", Input).value.strip()
            if not custom:
                return  # require a prompt for custom
        self.dismiss({"template_id": self._selected_id, "custom_prompt": custom})

    def action_cancel(self) -> None:
        self.dismiss(None)
