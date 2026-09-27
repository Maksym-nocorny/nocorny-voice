"""FairUpdateProcessor: per-user serialization, global media cap, no blocking of commands."""
import asyncio
import datetime

import pytest
from telegram import Chat, Message, Update, User, Voice

from utils.logging_setup import redact_secrets
from utils.update_processor import FairUpdateProcessor

_DATE = datetime.datetime(2026, 9, 27, tzinfo=datetime.timezone.utc)
_uid = 0


def _update(user_id: int, media: bool = True, chat_id: int = 0) -> Update:
    global _uid
    _uid += 1
    user = User(id=user_id, first_name="u", is_bot=False)
    chat = (Chat(id=chat_id, type=Chat.SUPERGROUP) if chat_id
            else Chat(id=user_id, type=Chat.PRIVATE))
    kwargs = {"voice": Voice("f", "uf", duration=5)} if media else {"text": "/stats"}
    msg = Message(message_id=_uid, date=_DATE, chat=chat, from_user=user, **kwargs)
    return Update(update_id=_uid, message=msg)


class _Recorder:
    def __init__(self):
        self.running = 0
        self.peak = 0
        self.log = []

    async def job(self, name, gate: asyncio.Event):
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.log.append(("start", name))
        await gate.wait()
        self.log.append(("end", name))
        self.running -= 1


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_same_user_is_serialized_in_order():
    p = FairUpdateProcessor(max_media=3)
    rec, gate = _Recorder(), asyncio.Event()
    tasks = [asyncio.create_task(p.process_update(_update(1), rec.job(i, gate)))
             for i in range(3)]
    await _settle()
    assert rec.running == 1 and p.busy_count() == 3
    gate.set()
    await asyncio.gather(*tasks)
    assert [n for kind, n in rec.log if kind == "start"] == [0, 1, 2]
    assert rec.peak == 1 and p.busy_count() == 0
    assert p._user_locks == {}


@pytest.mark.asyncio
async def test_backlog_of_one_user_does_not_block_another():
    p = FairUpdateProcessor(max_media=3)
    rec, gate = _Recorder(), asyncio.Event()
    flood = [asyncio.create_task(p.process_update(_update(1), rec.job(f"a{i}", gate)))
             for i in range(10)]
    other = asyncio.create_task(p.process_update(_update(2), rec.job("boss", gate)))
    await _settle()
    assert ("start", "boss") in rec.log
    assert rec.running == 2
    gate.set()
    await asyncio.gather(*flood, other)


@pytest.mark.asyncio
async def test_global_media_cap():
    p = FairUpdateProcessor(max_media=2)
    rec, gate = _Recorder(), asyncio.Event()
    tasks = [asyncio.create_task(p.process_update(_update(u), rec.job(u, gate)))
             for u in range(5)]
    await _settle()
    assert rec.running == 2
    gate.set()
    await asyncio.gather(*tasks)
    assert rec.peak == 2


@pytest.mark.asyncio
async def test_commands_never_wait_behind_media():
    p = FairUpdateProcessor(max_media=1)
    rec, gate, free = _Recorder(), asyncio.Event(), asyncio.Event()
    free.set()
    media = [asyncio.create_task(p.process_update(_update(1), rec.job(i, gate)))
             for i in range(3)]
    await _settle()
    await asyncio.wait_for(
        p.process_update(_update(1, media=False), rec.job("stats", free)), 1)
    assert ("end", "stats") in rec.log
    gate.set()
    await asyncio.gather(*media)


@pytest.mark.asyncio
async def test_handler_exception_releases_slots():
    p = FairUpdateProcessor(max_media=1)

    async def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await p.process_update(_update(1), boom())
    assert p.busy_count() == 0 and p._user_locks == {}
    done = asyncio.Event()

    async def ok():
        done.set()

    await asyncio.wait_for(p.process_update(_update(1), ok()), 1)
    assert done.is_set()


def test_redacts_bot_token_raw_and_encoded():
    tok = "1234567890:" + "A" * 35
    raw = f"POST https://api.telegram.org/bot{tok}/sendMessage"
    enc = f"GET https://api.telegram.org/file/bot{tok.replace(':', '%3A')}/voice/f.oga"
    assert "AAAAAAAAAA" not in redact_secrets(raw)
    assert "AAAAAAAAAA" not in redact_secrets(enc)
    assert "AAAAAAAAAA" not in redact_secrets(enc.replace("%3A", "%3a"))
    plain = "req=ab12cd34 user=111222333 chat=-1009998887776 mime=audio/ogg"
    assert redact_secrets(plain) == plain


@pytest.mark.asyncio
async def test_group_chat_is_serialized_per_chat():
    p = FairUpdateProcessor(max_media=3)
    rec, gate = _Recorder(), asyncio.Event()
    tasks = [asyncio.create_task(
        p.process_update(_update(u, chat_id=-100500), rec.job(u, gate)))
        for u in range(1, 4)]
    await _settle()
    assert rec.running == 1
    gate.set()
    await asyncio.gather(*tasks)


def test_zero_or_negative_cap_rejected():
    with pytest.raises(ValueError):
        FairUpdateProcessor(max_media=0)
