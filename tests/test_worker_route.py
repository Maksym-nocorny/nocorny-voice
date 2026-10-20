"""bot.install_worker_route: the /stt/worker route lands in PTB's tornado app."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import bot
import stt_remote


@pytest.fixture
def _restore_webhook_class():
    import telegram.ext._updater as updater_mod
    original = updater_mod.WebhookAppClass
    yield updater_mod
    updater_mod.WebhookAppClass = original


def test_route_installed_only_with_token(_restore_webhook_class):
    updater_mod = _restore_webhook_class
    before = updater_mod.WebhookAppClass
    assert bot.install_worker_route("") is False
    assert updater_mod.WebhookAppClass is before


def test_route_resolves_in_patched_app(_restore_webhook_class):
    updater_mod = _restore_webhook_class
    assert bot.install_worker_route("tok") is True
    app = updater_mod.WebhookAppClass("/hook-path", MagicMock(), asyncio.Queue(), "")
    assert app.settings["websocket_ping_interval"] == 30 == app.settings["websocket_ping_timeout"]

    def handler_for(path: str):
        req = MagicMock()
        req.path = path
        req.host = "localhost"
        req.host_name = "localhost"
        req.method = "GET"
        return app.find_handler(req)

    worker = handler_for(stt_remote.WORKER_PATH)
    assert worker is not None and worker.handler_class is stt_remote.WorkerSocket
    assert worker.handler_kwargs == {"token": "tok"}
    hook = handler_for("/hook-path")
    assert hook is not None and hook.handler_class.__name__ == "TelegramHandler"
