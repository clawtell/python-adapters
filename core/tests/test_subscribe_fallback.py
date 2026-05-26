"""Sticky poll fallback: after N consecutive SSE failures, subscribe
switches to poll() for fallback_ttl_seconds, then retries SSE."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import pytest

from clawtell_core.adapter import (
    AgentReply,
    ChatTarget,
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)
from clawtell_core.subscribe import subscribe


class _FailingStreamClient:
    """client.stream() raises; client.poll() returns one msg per call."""

    def __init__(self) -> None:
        self.stream_attempts = 0
        self.poll_calls = 0
        self.acked: list[str] = []
        self.sent: list[tuple] = []

    def stream(self, **kw):
        self.stream_attempts += 1
        raise ConnectionError("simulated SSE failure")

    def poll(self, *, timeout: int = 30, limit: int = 50) -> dict:
        self.poll_calls += 1
        return {
            "messages": [
                {
                    "id": f"poll_msg_{self.poll_calls}",
                    "from": "tell/x",
                    "subject": "",
                    "body": "from poll",
                    "created_at": "2026-05-25T00:00:00Z",
                    "auto_reply_eligible": False,
                }
            ]
        }

    def ack(self, ids: list[str], **kw) -> None:
        self.acked.extend(ids)

    def send(self, to: str, body: str, subject: Optional[str] = None) -> dict:
        self.sent.append((to, body, subject))
        return {"status": "ok"}


class _MinimalAdapter(ClawTellAdapter):
    def __init__(self) -> None:
        self.forwarded: list[HumanNotification] = []

    async def inject(self, msg: InboundMessage) -> Optional[AgentReply]:
        return None

    async def forward(
        self, notification: HumanNotification, target: ChatTarget
    ) -> bool:
        self.forwarded.append(notification)
        return True


@pytest.mark.asyncio
async def test_subscribe_falls_back_to_poll_after_three_sse_failures(caplog):
    """3 SSE failures → log warning → switch to poll for fallback_ttl_seconds.
    Messages delivered via poll during fallback."""
    client = _FailingStreamClient()
    adapter = _MinimalAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="T"))
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(3.0)
        stop.set()

    asyncio.create_task(stopper())
    caplog.set_level(logging.WARNING, logger="clawtell_core.transport")
    await subscribe(
        client,
        adapter,
        empty_poll_sleep=0.05,
        drain_interval=999,
        stop_event=stop,
        sse_failure_threshold=3,
        sse_backoff_base_ms=10,
        sse_backoff_ceiling_ms=20,
        fallback_ttl_seconds=1.5,
    )
    assert client.stream_attempts >= 3
    assert client.poll_calls >= 1
    assert any(
        "consecutive failures, switching to poll fallback" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_force_poll_env_skips_sse_entirely(monkeypatch):
    monkeypatch.setenv("CLAWTELL_FORCE_POLL", "1")
    client = _FailingStreamClient()
    adapter = _MinimalAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="T"))
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.4)
        stop.set()

    asyncio.create_task(stopper())
    await subscribe(
        client,
        adapter,
        empty_poll_sleep=0.05,
        drain_interval=999,
        stop_event=stop,
    )
    assert client.stream_attempts == 0
    assert client.poll_calls >= 1


@pytest.mark.asyncio
async def test_force_poll_kwarg_skips_sse_entirely():
    client = _FailingStreamClient()
    adapter = _MinimalAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="T"))
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.4)
        stop.set()

    asyncio.create_task(stopper())
    await subscribe(
        client,
        adapter,
        force_poll=True,
        empty_poll_sleep=0.05,
        drain_interval=999,
        stop_event=stop,
    )
    assert client.stream_attempts == 0
    assert client.poll_calls >= 1
