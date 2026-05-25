"""Hermes adapter behavior: fresh-per-message default, LRU pool when
enabled, sync/async .chat() handled, refusal returned on factory error.

Note: ``hermes-agent`` is NOT imported here — the tests use a stub agent
class so the adapter can be exercised without the framework installed."""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from clawtell_core.adapter import ChatTarget, InboundMessage
from clawtell_hermes import HermesAdapter


class _StubAgent:
    instances: list["_StubAgent"] = []

    def __init__(self, reply: str = "ok") -> None:
        self._reply = reply
        self.calls: list[str] = []
        _StubAgent.instances.append(self)

    def chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._reply


@pytest.fixture(autouse=True)
def _reset_instances():
    _StubAgent.instances.clear()
    yield
    _StubAgent.instances.clear()


async def _noop_sender(chat_id: str, text: str) -> None:
    return None


def _msg(i: str, sender: str = "alice", body: str = "hello") -> InboundMessage:
    return InboundMessage(
        id=i,
        from_name=sender,
        subject="hi",
        body=body,
        received_at="2026-05-25T00:00:00Z",
        auto_reply_eligible=True,
    )


@pytest.mark.asyncio
async def test_fresh_agent_per_message_by_default():
    adapter = HermesAdapter(agent_factory=_StubAgent, sender=_noop_sender)
    await adapter.inject(_msg("m1", "alice"))
    await adapter.inject(_msg("m2", "alice"))
    assert len(_StubAgent.instances) == 2  # not shared


@pytest.mark.asyncio
async def test_pool_reuses_agent_per_sender():
    adapter = HermesAdapter(
        agent_factory=_StubAgent, sender=_noop_sender, agent_pool_size=3
    )
    await adapter.inject(_msg("m1", "alice"))
    await adapter.inject(_msg("m2", "alice"))
    await adapter.inject(_msg("m3", "bob"))
    assert len(_StubAgent.instances) == 2  # one per sender


@pytest.mark.asyncio
async def test_pool_evicts_least_recently_used():
    adapter = HermesAdapter(
        agent_factory=_StubAgent, sender=_noop_sender, agent_pool_size=2
    )
    await adapter.inject(_msg("m1", "alice"))
    await adapter.inject(_msg("m2", "bob"))
    await adapter.inject(_msg("m3", "carol"))   # evicts alice
    await adapter.inject(_msg("m4", "alice"))   # alice gets new agent
    assert len(_StubAgent.instances) == 4


@pytest.mark.asyncio
async def test_prompt_includes_clawtell_envelope():
    captured = {}

    class _Recorder:
        def chat(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return "pong"

    adapter = HermesAdapter(agent_factory=_Recorder, sender=_noop_sender)
    await adapter.inject(_msg("m1", "alice", body="ping?"))
    p = captured["prompt"]
    assert "[Incoming ClawTell message from tell/alice]" in p
    assert "Subject: hi" in p
    assert "ping?" in p


@pytest.mark.asyncio
async def test_async_chat_method_awaited():
    class _AsyncAgent:
        async def chat(self, prompt: str) -> str:
            await asyncio.sleep(0)
            return "async-reply"

    adapter = HermesAdapter(agent_factory=_AsyncAgent, sender=_noop_sender)
    reply = await adapter.inject(_msg("m1"))
    assert reply is not None
    assert reply.text == "async-reply"


@pytest.mark.asyncio
async def test_empty_reply_returns_none():
    class _EmptyAgent:
        def chat(self, prompt: str) -> str:
            return ""

    adapter = HermesAdapter(agent_factory=_EmptyAgent, sender=_noop_sender)
    assert await adapter.inject(_msg("m1")) is None


@pytest.mark.asyncio
async def test_chat_exception_returns_refusal():
    class _BrokenAgent:
        def chat(self, prompt: str) -> str:
            raise RuntimeError("model timeout")

    adapter = HermesAdapter(agent_factory=_BrokenAgent, sender=_noop_sender)
    reply = await adapter.inject(_msg("m1"))
    assert reply is not None
    assert reply.refusal is not None
    assert "model timeout" in reply.refusal


@pytest.mark.asyncio
async def test_factory_timeout_returns_refusal():
    """Hermes ctor hangs (model load, memory pressure) → adapter must NOT
    block the subscribe loop forever. It returns a refusal AgentReply so
    the inbound can still be acked + forwarded to the human."""
    import time

    def slow_factory():
        time.sleep(2.0)  # blocks the to_thread worker
        return _StubAgent()

    adapter = HermesAdapter(
        agent_factory=slow_factory,
        sender=_noop_sender,
        factory_timeout=0.2,
    )
    reply = await adapter.inject(_msg("m1"))
    assert reply is not None
    assert reply.refusal is not None
    assert "factory_timeout" not in reply.refusal  # human-readable message
    assert "0.2s" in reply.refusal or "did not return" in reply.refusal


@pytest.mark.asyncio
async def test_factory_timeout_zero_disables():
    """Explicit opt-out — long ctors are allowed when timeout is 0."""
    import time

    def slow_factory():
        time.sleep(0.3)
        return _StubAgent()

    adapter = HermesAdapter(
        agent_factory=slow_factory,
        sender=_noop_sender,
        factory_timeout=0,
    )
    reply = await adapter.inject(_msg("m1"))
    assert reply is not None
    assert reply.refusal is None
    assert reply.text == "ok"


@pytest.mark.asyncio
async def test_forward_invokes_sender_with_formatted_text():
    sent: list[tuple[str, str]] = []

    async def sender(chat_id: str, text: str) -> None:
        sent.append((chat_id, text))

    adapter = HermesAdapter(agent_factory=_StubAgent, sender=sender)
    adapter.bind_chat(ChatTarget(channel="telegram", chat_id="TEST"))
    from clawtell_core.adapter import AgentReply, HumanNotification

    await adapter.forward(
        HumanNotification(inbound=_msg("m1"), reply=AgentReply(text="pong")),
        ChatTarget(channel="telegram", chat_id="TEST"),
    )
    assert sent[0][0] == "TEST"
    assert "pong" in sent[0][1]
    assert "tell/alice" in sent[0][1]
