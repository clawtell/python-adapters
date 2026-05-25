"""TelegramBridge: capture first chat id, bind it to the adapter,
persist to ``~/.clawtell/chat.json``, replay persisted chat on restart.

Uses ``httpx.MockTransport`` to stub Telegram's getUpdates without a
network round-trip."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from clawtell_core.adapter import (
    AgentReply,
    ChatTarget,
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)
from clawtell_telegram import TelegramBridge, load_persisted_chat
from clawtell_telegram.bridge import _state_path


class _StubAdapter(ClawTellAdapter):
    async def inject(self, msg: InboundMessage):
        return None

    async def forward(self, n: HumanNotification, t: ChatTarget) -> bool:
        return True


def _updates_handler(updates_by_call: list[list[dict]]):
    """MockTransport handler that returns successive update batches."""
    call_idx = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = call_idx["i"]
        call_idx["i"] += 1
        if idx >= len(updates_by_call):
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(
            200, json={"ok": True, "result": updates_by_call[idx]}
        )

    return handler


@pytest.mark.asyncio
async def test_first_update_binds_chat_and_persists(
    isolated_clawtell_home: Path,
):
    handler = _updates_handler(
        [
            [
                {
                    "update_id": 100,
                    "message": {
                        "message_id": 1,
                        "chat": {"id": 99999, "type": "private"},
                        "text": "hi",
                    },
                }
            ]
        ]
    )
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    bridge = TelegramBridge(bot_token="TEST", poll_timeout=0, client=client)
    adapter = _StubAdapter()
    bridge.attach(adapter)

    task = asyncio.create_task(bridge.run())
    # Let the bridge consume one batch then stop.
    await asyncio.sleep(0.1)
    bridge.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert adapter.get_chat() is not None
    assert adapter.get_chat().chat_id == "99999"
    persisted = json.loads(_state_path().read_text(encoding="utf-8"))
    assert persisted["chat_id"] == "99999"


@pytest.mark.asyncio
async def test_attach_restores_persisted_chat_without_update(
    isolated_clawtell_home: Path,
):
    # Pre-seed the persisted chat as if a previous run captured it.
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps({"channel": "telegram", "chat_id": "55555"}),
        encoding="utf-8",
    )
    bridge = TelegramBridge(bot_token="TEST")
    adapter = _StubAdapter()
    bridge.attach(adapter)
    chat = adapter.get_chat()
    assert chat is not None
    assert chat.chat_id == "55555"


def test_load_persisted_chat_missing_returns_none(
    isolated_clawtell_home: Path,
):
    assert load_persisted_chat() is None


@pytest.mark.asyncio
async def test_subsequent_updates_update_bound_chat(
    isolated_clawtell_home: Path,
):
    handler = _updates_handler(
        [
            [{"update_id": 1, "message": {"chat": {"id": 100, "type": "private"}}}],
            [{"update_id": 2, "message": {"chat": {"id": 200, "type": "private"}}}],
        ]
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bridge = TelegramBridge(bot_token="T", poll_timeout=0, client=client)
    adapter = _StubAdapter()
    bridge.attach(adapter)

    task = asyncio.create_task(bridge.run())
    await asyncio.sleep(0.2)
    bridge.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert adapter.get_chat().chat_id == "200"


@pytest.mark.asyncio
async def test_run_without_attach_raises():
    bridge = TelegramBridge(bot_token="T")
    with pytest.raises(RuntimeError, match="attach"):
        await bridge.run()
