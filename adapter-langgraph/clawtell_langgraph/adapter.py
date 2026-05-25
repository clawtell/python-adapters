"""LangGraphAdapter — inject ClawTell messages into a compiled graph.

Per-``thread_id`` ``asyncio.Lock`` serialization (LangGraph is not
documented as thread-safe for concurrent invokes on the same thread).
Detect ``interrupt()``-paused threads via ``graph.aget_state()`` and
branch between fresh-turn invoke vs. ``Command(resume=...)``.

The adapter does not import langgraph at module load — heavy imports are
deferred to first ``inject()`` call so users without langgraph installed
can still build the adapter object if their flow doesn't need it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from clawtell_core.adapter import (
    AgentReply,
    ChatTarget,
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)
from clawtell_core.formatter import format_inbound, format_reply

log = logging.getLogger(__name__)

ThreadIdResolver = Callable[[InboundMessage], str]
TelegramSender = Callable[[str, str], Awaitable[None]]


class LangGraphAdapter(ClawTellAdapter):
    def __init__(
        self,
        graph: Any,
        sender: TelegramSender,
        *,
        thread_id_resolver: Optional[ThreadIdResolver] = None,
    ) -> None:
        """
        Args:
            graph: compiled LangGraph (must have a checkpointer for
                interrupt/resume support).
            sender: async (chat_id, text) -> None.
            thread_id_resolver: maps inbound message → thread_id.
                Defaults to ``msg.from_name`` so each ClawTell sender gets
                its own conversation thread.
        """
        self._graph = graph
        self._sender = sender
        self._resolve = thread_id_resolver or (lambda m: m.from_name)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, thread_id: str) -> asyncio.Lock:
        lock = self._locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_id] = lock
        return lock

    async def inject(self, msg: InboundMessage) -> Optional[AgentReply]:
        thread_id = self._resolve(msg)
        lock = self._lock_for(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            async with lock:
                result = await self._invoke_or_resume(msg, config)
        except Exception as e:
            log.exception("langgraph inject failed for msg %s", msg.id)
            return AgentReply(text="", refusal=f"graph error: {e}")
        return self._extract_reply(result)

    async def _invoke_or_resume(self, msg: InboundMessage, config: dict) -> Any:
        # Defer langgraph imports to runtime so build-time import doesn't
        # require the dep on systems that only use HermesAdapter.
        from langchain_core.messages import HumanMessage
        from langgraph.types import Command

        snap = await self._graph.aget_state(config)
        paused = bool(getattr(snap, "next", None)) and any(
            getattr(t, "interrupts", None) for t in (getattr(snap, "tasks", None) or [])
        )
        if paused:
            log.debug("thread %s paused at interrupt — resuming", config["configurable"]["thread_id"])
            return await self._graph.ainvoke(Command(resume=msg.body), config)
        return await self._graph.ainvoke(
            {"messages": [HumanMessage(content=msg.body)]}, config
        )

    @staticmethod
    def _extract_reply(result: Any) -> Optional[AgentReply]:
        from langchain_core.messages import AIMessage

        messages = (result or {}).get("messages") if isinstance(result, dict) else None
        if not messages:
            return None
        for m in reversed(messages):
            if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
                content = m.content if isinstance(m.content, str) else str(m.content)
                return AgentReply(text=content.strip())
        return None

    async def forward(
        self, notification: HumanNotification, target: ChatTarget
    ) -> bool:
        if notification.reply is not None:
            text = format_reply(notification.reply, notification.inbound)
        else:
            text = format_inbound(notification.inbound)
        try:
            await self._sender(target.chat_id, text)
            return True
        except Exception as e:
            log.warning(
                "telegram send failed for chat %s: %s", target.chat_id, e
            )
            return False
