"""SSE-first transport happy path: when client.stream() is available and
healthy, subscribe consumes from it and never falls back to poll()."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from clawtell_core.adapter import (
    AgentReply,
    ChatTarget,
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)
from clawtell_core.subscribe import subscribe


class _StreamingStubClient:
    """Stub that delivers messages via client.stream() (a sync generator).
    Tracks whether poll() was ever called so the test can assert SSE primary."""

    def __init__(self, stream_batches: list[list[dict]]) -> None:
        self._batches = list(stream_batches)
        self.poll_calls = 0
        self.acked: list[str] = []
        self.ack_kwargs: list[dict] = []
        self.sent: list[tuple] = []

    def stream(self, **kw):
        while self._batches:
            for msg in self._batches.pop(0):
                yield msg

    def poll(self, *, timeout: int = 30, limit: int = 50) -> dict:
        self.poll_calls += 1
        return {"messages": []}

    def ack(self, ids: list[str], **kw) -> None:
        self.acked.extend(ids)
        self.ack_kwargs.append(kw)

    def send(self, to: str, body: str, subject: Optional[str] = None) -> dict:
        self.sent.append((to, body, subject))
        return {"status": "delivered"}


class _RecordingAdapter(ClawTellAdapter):
    def __init__(self, reply_text: Optional[str] = None) -> None:
        self._reply_text = reply_text
        self.injected: list[InboundMessage] = []
        self.forwarded: list[HumanNotification] = []

    async def inject(self, msg: InboundMessage) -> Optional[AgentReply]:
        self.injected.append(msg)
        return AgentReply(text=self._reply_text) if self._reply_text else None

    async def forward(
        self, notification: HumanNotification, target: ChatTarget
    ) -> bool:
        self.forwarded.append(notification)
        return True


def _raw_msg(i: str, *, eligible: bool, sender: str = "alice") -> dict:
    return {
        "id": i,
        "from": f"tell/{sender}",
        "subject": "hi",
        "body": "hello",
        "created_at": "2026-05-25T00:00:00Z",
        "auto_reply_eligible": eligible,
    }


@pytest.mark.asyncio
async def test_subscribe_uses_sse_when_force_poll_false():
    """Healthy SSE: subscribe pulls from client.stream() exclusively. Zero
    poll() calls. Three-branch dispatch still applies."""
    msgs = [_raw_msg("sse_1", eligible=False)]
    client = _StreamingStubClient(stream_batches=[msgs])
    adapter = _RecordingAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))

    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.3)
        stop.set()

    asyncio.create_task(stopper())
    await subscribe(
        client,
        adapter,
        empty_poll_sleep=0.05,
        drain_interval=999,
        stop_event=stop,
        sse_backoff_base_ms=10,
        sse_backoff_ceiling_ms=20,
    )
    assert client.poll_calls == 0
    assert client.acked == ["sse_1"]
    assert len(adapter.forwarded) == 1


@pytest.mark.asyncio
async def test_subscribe_acks_streamed_messages_with_prefer_sse_true():
    """Channel-plugin parity: streamed messages ACK against the SSE host first.
    The adapter passes prefer_sse=True to every client.ack() call."""
    msgs = [_raw_msg("kw_check", eligible=False)]
    client = _StreamingStubClient(stream_batches=[msgs])
    adapter = _RecordingAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))

    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.3)
        stop.set()

    asyncio.create_task(stopper())
    await subscribe(
        client,
        adapter,
        empty_poll_sleep=0.05,
        drain_interval=999,
        stop_event=stop,
        sse_backoff_base_ms=10,
        sse_backoff_ceiling_ms=20,
    )
    assert client.ack_kwargs and client.ack_kwargs[0].get("prefer_sse") is True
