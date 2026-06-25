"""Tests for Dialogues TUI screen lifecycle."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from textual.app import App, ComposeResult
from textual.widgets import Static

from dialogues.credentials import load_credentials, save_credentials, clear_credentials
import dialogues.credentials as cred_mod
from tui.widgets.dialogues_screen import DialoguesScreen


class _HostApp(App):
    def compose(self) -> ComposeResult:
        yield Static("main")


def test_escape_closes_dialogues_screen_without_detaching(tmp_path, monkeypatch):
    monkeypatch.setattr(cred_mod, "DATA_DIR", tmp_path)
    clear_credentials()
    save_credentials(
        plugin_attach_token="tok",
        resource_id="res",
        cp_url="https://cp.example.com",
    )
    detach_callbacks: list[bool] = []

    async def run() -> None:
        app = _HostApp()
        client = MagicMock()
        client.push_enabled = False
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                DialoguesScreen(
                    client,
                    on_detach=lambda: detach_callbacks.append(True),
                )
            )
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert load_credentials() is not None
            assert detach_callbacks == []

    asyncio.run(run())


def test_detach_menu_option_clears_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(cred_mod, "DATA_DIR", tmp_path)
    clear_credentials()
    save_credentials(
        plugin_attach_token="tok",
        resource_id="res",
        cp_url="https://cp.example.com",
    )
    detach_callbacks: list[bool] = []

    async def run() -> None:
        app = _HostApp()
        client = MagicMock()
        client.push_enabled = False
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                DialoguesScreen(
                    client,
                    on_detach=lambda: detach_callbacks.append(True),
                )
            )
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        assert load_credentials() is None
        assert detach_callbacks == [True]

    asyncio.run(run())
