"""bot.standby_web_app: webhook POST feeds PTB's queue, `/` answers, worker route present."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
import tornado.httpclient
import tornado.web
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

import bot
import stt_remote


@pytest.mark.asyncio
async def test_standby_app_routes(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "123:TOKEN")
    monkeypatch.setattr(bot, "STT_SERVER_TOKEN", "wtok")
    app = MagicMock()
    app.bot = MagicMock()
    app.update_queue = asyncio.Queue()
    web = bot.standby_web_app(app)
    server = HTTPServer(web)
    socks = bind_sockets(0, "127.0.0.1")
    port = socks[0].getsockname()[1]
    server.add_sockets(socks)
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        r = await client.fetch(f"http://127.0.0.1:{port}/")
        assert r.code == 200 and r.body.strip() == b"ok"
        update = {"update_id": 7, "message": {"message_id": 1, "date": 0,
                                               "chat": {"id": 5, "type": "private"}, "text": "hi"}}
        r = await client.fetch(f"http://127.0.0.1:{port}/123:TOKEN", method="POST",
                               body=json.dumps(update), headers={"Content-Type": "application/json"})
        assert r.code == 200
        queued = await asyncio.wait_for(app.update_queue.get(), 1)
        assert queued.update_id == 7
        # wrong content type is refused like PTB does
        r = await client.fetch(f"http://127.0.0.1:{port}/123:TOKEN", method="POST",
                               body="{}", raise_error=False)
        assert r.code == 403
        r = await client.fetch(f"http://127.0.0.1:{port}{stt_remote.WORKER_PATH}", raise_error=False)
        assert r.code == 401   # worker route is there, bearer required
        # lease: beat without token refused, with token accepted and remembered
        import bot_lease
        monkeypatch.setattr(bot_lease, "STT_SERVER_TOKEN", "wtok")
        bot_lease.standby = bot_lease.StandbyState()
        r = await client.fetch(f"http://127.0.0.1:{port}{bot_lease.BEAT_PATH}", method="POST",
                               body="{}", raise_error=False)
        assert r.code == 401
        r = await client.fetch(f"http://127.0.0.1:{port}{bot_lease.BEAT_PATH}", method="POST",
                               body='{"instance": "m1"}', headers={"Authorization": "Bearer wtok"})
        assert r.code == 200 and json.loads(r.body)["holding"] is False
        assert bot_lease.standby.beat_age() < 1
        r = await client.fetch(f"http://127.0.0.1:{port}{bot_lease.STATUS_PATH}",
                               headers={"Authorization": "Bearer wtok"})
        assert json.loads(r.body)["primary"]["instance"] == "m1"
        # draining: webhook POSTs are refused with 503 so Telegram redelivers
        monkeypatch.setattr(bot, "_draining", True)
        r = await client.fetch(f"http://127.0.0.1:{port}/123:TOKEN", method="POST",
                               body=json.dumps(update), headers={"Content-Type": "application/json"},
                               raise_error=False)
        assert r.code == 503 and app.update_queue.empty()
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_quiet_cloud_bot_initializes_without_get_me(monkeypatch):
    from telegram.request import HTTPXRequest
    monkeypatch.setattr(bot, "BOT_USERNAME", "Some_bot")
    qb = bot.QuietCloudBot("4242:secret", request=HTTPXRequest(), get_updates_request=HTTPXRequest())
    calls = []
    # TelegramObject forbids instance attribute writes: patch the class method
    monkeypatch.setattr(bot.QuietCloudBot, "get_me", lambda self, *a, **k: calls.append("get_me"))
    await qb.initialize()
    assert calls == [] and qb.id == 4242 and qb.username == "Some_bot"
    await qb.shutdown()


def test_main_refuses_to_start_primary_with_stop_flag(monkeypatch, tmp_path):
    import bot_lease
    flag = tmp_path / ".power_low"; flag.write_text("")
    monkeypatch.setattr(bot_lease, "BOT_STOP_FLAG_FILE", str(flag))
    monkeypatch.setattr(bot, "BOT_INSTANCE", "m1")
    monkeypatch.setattr(bot, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "1:t")
    monkeypatch.setattr(bot, "setup_logging", lambda: None)
    with pytest.raises(SystemExit) as ei:
        bot.main()
    assert ei.value.code == 0


@pytest.mark.asyncio
async def test_conflict_marks_reclaim_owed(monkeypatch):
    import bot_lease
    from telegram.error import Conflict
    bot_lease._reclaim_owed = False
    ctx = MagicMock(); ctx.error = Conflict("terminated by other getUpdates request")
    await bot._global_error_handler(None, ctx)
    assert bot_lease.reclaim_owed()
    bot_lease._reclaim_owed = False
