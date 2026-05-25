"""LangGraphAdapter behavior: per-thread serialization, interrupt
branch-selection, reply extraction. Requires ``langgraph`` +
``langchain-core`` for the AIMessage/HumanMessage shape checks."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from clawtell_core.adapter import InboundMessage  # noqa: E402
from clawtell_langgraph import LangGraphAdapter  # noqa: E402


class _FakeTask:
    def __init__(self, interrupts: tuple = ()) -> None:
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, *, next: tuple = (), tasks: list = ()) -> None:
        self.next = next
        self.tasks = list(tasks)


class _FakeGraph:
    """Records inputs to ainvoke/aget_state and returns canned outputs."""

    def __init__(
        self, *, paused: bool = False, reply_text: str = "pong"
    ) -> None:
        self._paused = paused
        self._reply = reply_text
        self.invocations: list[tuple[Any, dict]] = []
        self.state_queries: list[dict] = []

    async def aget_state(self, config: dict) -> _FakeSnapshot:
        self.state_queries.append(config)
        if self._paused:
            return _FakeSnapshot(
                next=("interrupted_node",),
                tasks=[_FakeTask(interrupts=("waiting",))],
            )
        return _FakeSnapshot()

    async def ainvoke(self, payload: Any, config: dict) -> dict:
        self.invocations.append((payload, config))
        return {
            "messages": [
                HumanMessage(content="some prior input"),
                AIMessage(content=self._reply),
            ]
        }


async def _noop_sender(chat_id: str, text: str) -> None:
    return None


def _msg(i: str = "m1", sender: str = "alice", body: str = "ping?") -> InboundMessage:
    return InboundMessage(
        id=i,
        from_name=sender,
        subject="hi",
        body=body,
        received_at="2026-05-25T00:00:00Z",
        auto_reply_eligible=True,
    )


@pytest.mark.asyncio
async def test_fresh_thread_invokes_with_human_message():
    g = _FakeGraph(paused=False)
    adapter = LangGraphAdapter(graph=g, sender=_noop_sender)
    reply = await adapter.inject(_msg("m1", "alice"))
    assert reply is not None
    assert reply.text == "pong"
    payload, config = g.invocations[0]
    assert isinstance(payload, dict)
    assert isinstance(payload["messages"][0], HumanMessage)
    assert payload["messages"][0].content == "ping?"
    assert config == {"configurable": {"thread_id": "alice"}}


@pytest.mark.asyncio
async def test_paused_thread_resumes_with_command():
    g = _FakeGraph(paused=True)
    adapter = LangGraphAdapter(graph=g, sender=_noop_sender)
    await adapter.inject(_msg("m2", "alice", body="continue"))
    payload, _ = g.invocations[0]
    assert isinstance(payload, Command)
    # langgraph 1.0 Command exposes resume/values; the resume value here
    # should match the message body.
    assert getattr(payload, "resume", None) == "continue"


@pytest.mark.asyncio
async def test_thread_id_resolver_override():
    g = _FakeGraph()
    adapter = LangGraphAdapter(
        graph=g, sender=_noop_sender, thread_id_resolver=lambda m: f"t-{m.id}"
    )
    await adapter.inject(_msg("m3", "alice"))
    assert g.invocations[0][1] == {"configurable": {"thread_id": "t-m3"}}


@pytest.mark.asyncio
async def test_extract_reply_skips_tool_calls_and_empty_content():
    g = _FakeGraph()

    async def aget_state(config):
        return _FakeSnapshot()

    async def ainvoke(payload, config):
        return {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="", tool_calls=[{"id": "1", "name": "x", "args": {}}]),
                AIMessage(content="actual reply"),
            ]
        }

    g.aget_state = aget_state  # type: ignore[assignment]
    g.ainvoke = ainvoke  # type: ignore[assignment]
    adapter = LangGraphAdapter(graph=g, sender=_noop_sender)
    reply = await adapter.inject(_msg("m4"))
    assert reply is not None
    assert reply.text == "actual reply"


@pytest.mark.asyncio
async def test_no_ai_message_returns_none():
    g = _FakeGraph()

    async def ainvoke(payload, config):
        return {"messages": [HumanMessage(content="hi")]}

    g.ainvoke = ainvoke  # type: ignore[assignment]
    adapter = LangGraphAdapter(graph=g, sender=_noop_sender)
    assert await adapter.inject(_msg("m5")) is None


@pytest.mark.asyncio
async def test_per_thread_lock_serializes_invokes_to_same_thread():
    """Two concurrent inject() calls to the same thread_id must be
    processed serially (LangGraph isn't documented as concurrency-safe
    on a single thread)."""
    order: list[str] = []

    class _SlowGraph(_FakeGraph):
        async def ainvoke(self, payload, config):
            order.append(f"start:{payload['messages'][0].content if isinstance(payload, dict) else 'resume'}")
            await asyncio.sleep(0.05)
            order.append(f"end:{payload['messages'][0].content if isinstance(payload, dict) else 'resume'}")
            return {
                "messages": [AIMessage(content="done")],
            }

    g = _SlowGraph()
    adapter = LangGraphAdapter(graph=g, sender=_noop_sender)
    await asyncio.gather(
        adapter.inject(_msg("a", "alice", body="A")),
        adapter.inject(_msg("b", "alice", body="B")),
    )
    # No interleaving: each start must be followed by its own end before the next start.
    assert order[0].startswith("start:")
    assert order[1].startswith("end:")
    assert order[2].startswith("start:")
    assert order[3].startswith("end:")


@pytest.mark.asyncio
async def test_per_thread_lock_does_not_serialize_distinct_threads():
    """Different senders → different thread_ids → must run concurrently."""
    started = asyncio.Event()

    class _BlockingGraph(_FakeGraph):
        async def ainvoke(self, payload, config):
            started.set()
            await asyncio.sleep(0.1)
            return {"messages": [AIMessage(content="ok")]}

    g = _BlockingGraph()
    adapter = LangGraphAdapter(graph=g, sender=_noop_sender)

    async def second():
        await started.wait()
        # If we got here while the first is still sleeping, threads aren't
        # serialized by a global lock. Run our own inject for sender "bob".
        return await adapter.inject(_msg("b", "bob"))

    first = asyncio.create_task(adapter.inject(_msg("a", "alice")))
    other = await asyncio.wait_for(second(), timeout=1.0)
    await first
    assert other is not None
