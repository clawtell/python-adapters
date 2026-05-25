"""Three-branch dispatch under ``_process``: (1) no chat → queue, no ack;
(2) ineligible → forward inbound, ack, skip agent; (3) eligible → inject,
forward(reply), send, ack. Plus dedup-window behavior."""

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
from clawtell_core.queue import InboxQueue
from clawtell_core.subscribe import _DedupWindow, _process, subscribe


class _StubClient:
    def __init__(self, batches: Optional[list[list[dict]]] = None) -> None:
        self._batches = list(batches or [])
        self.acked: list[str] = []
        self.sent: list[tuple[str, str, Optional[str]]] = []

    def poll(self, *, timeout: int = 30, limit: int = 50) -> dict:
        if self._batches:
            return {"messages": self._batches.pop(0)}
        return {"messages": []}

    def ack(self, ids: list[str]) -> None:
        self.acked.extend(ids)

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
async def test_branch_1_no_chat_bound_queues_without_ack(isolated_clawtell_home):
    client = _StubClient()
    adapter = _RecordingAdapter()
    queue = InboxQueue()
    msg = InboundMessage.from_dict(_raw_msg("m1", eligible=True))
    await _process(client, adapter, queue, msg)
    assert client.acked == []                 # no ack — server will redeliver
    assert client.sent == []
    assert adapter.injected == []             # agent NOT invoked
    assert adapter.forwarded == []
    assert [m["id"] for m in queue.get_pending()] == ["m1"]


@pytest.mark.asyncio
async def test_branch_2_ineligible_forwards_and_acks_without_inject(
    isolated_clawtell_home,
):
    client = _StubClient()
    adapter = _RecordingAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))
    queue = InboxQueue()
    msg = InboundMessage.from_dict(_raw_msg("m2", eligible=False))
    await _process(client, adapter, queue, msg)
    assert client.acked == ["m2"]
    assert client.sent == []
    assert adapter.injected == []             # agent NOT invoked
    assert len(adapter.forwarded) == 1
    assert adapter.forwarded[0].reply is None  # inbound only — no reply attached
    assert queue.get_pending() == []


@pytest.mark.asyncio
async def test_branch_3_eligible_invokes_forwards_sends_and_acks(
    isolated_clawtell_home,
):
    client = _StubClient()
    adapter = _RecordingAdapter(reply_text="pong")
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))
    queue = InboxQueue()
    msg = InboundMessage.from_dict(_raw_msg("m3", eligible=True))
    await _process(client, adapter, queue, msg)
    assert client.acked == ["m3"]
    assert client.sent == [("alice", "pong", "Re: hi")]
    assert len(adapter.injected) == 1
    assert len(adapter.forwarded) == 1
    assert adapter.forwarded[0].reply is not None
    assert adapter.forwarded[0].reply.text == "pong"


@pytest.mark.asyncio
async def test_branch_3_eligible_with_none_reply_still_forwards_and_acks(
    isolated_clawtell_home,
):
    """Agent declined (returned None) — still forward inbound + ack;
    just no clawtell-side reply send."""
    client = _StubClient()
    adapter = _RecordingAdapter(reply_text=None)
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))
    queue = InboxQueue()
    msg = InboundMessage.from_dict(_raw_msg("m4", eligible=True))
    await _process(client, adapter, queue, msg)
    assert client.acked == ["m4"]
    assert client.sent == []                  # no auto-reply on the wire
    assert len(adapter.injected) == 1
    assert len(adapter.forwarded) == 1


def test_dedup_window_eviction_is_fifo():
    w = _DedupWindow(size=3)
    for i in ("a", "b", "c"):
        w.add(i)
    assert all(w.seen(i) for i in ("a", "b", "c"))
    w.add("d")
    assert not w.seen("a")
    assert all(w.seen(i) for i in ("b", "c", "d"))


@pytest.mark.asyncio
async def test_subscribe_dedups_redelivered_id(isolated_clawtell_home):
    """If the same message id arrives twice (e.g. ack race), the second
    is silently re-acked and dispatch happens only once."""
    raw = _raw_msg("dup", eligible=False)
    client = _StubClient(batches=[[raw], [raw]])
    adapter = _RecordingAdapter()
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))

    stop = asyncio.Event()

    async def run_and_stop():
        # Let the loop process both batches, then stop.
        await asyncio.sleep(0.5)
        stop.set()

    asyncio.create_task(run_and_stop())
    await subscribe(
        client,
        adapter,
        poll_timeout=0,
        empty_poll_sleep=0.05,
        drain_interval=999,
        stop_event=stop,
    )
    assert len(adapter.forwarded) == 1        # dispatched only once
    assert client.acked.count("dup") >= 1     # re-ack on dup is OK
