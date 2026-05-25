"""HermesAdapter — instantiate an ``AIAgent`` per inbound ClawTell message.

Hermes docs explicitly forbid sharing an ``AIAgent`` instance across
threads. The default mode here is "new instance per message" (safest).
An optional bounded LRU pool keyed by sender name preserves conversation
history when memory cost is acceptable.

The adapter does not import ``hermes`` at module load — the user supplies
an ``agent_factory`` callable that returns whatever Hermes API surface
their build prefers (``AIAgent`` or ``run_conversation``-based wrapper).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
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

AgentFactory = Callable[[], Any]
"""() -> a Hermes agent instance with a ``.chat(prompt: str) -> str`` method,
or any object exposing ``.chat`` returning either str or awaitable[str]."""

TelegramSender = Callable[[str, str], Awaitable[None]]
"""async (chat_id, text) -> None"""


class HermesAdapter(ClawTellAdapter):
    def __init__(
        self,
        agent_factory: AgentFactory,
        sender: TelegramSender,
        *,
        agent_pool_size: int = 0,
        factory_timeout: float = 30.0,
    ) -> None:
        self._factory = agent_factory
        self._sender = sender
        self._pool_size = max(0, int(agent_pool_size))
        self._pool: OrderedDict[str, Any] = OrderedDict()
        self._pool_lock = asyncio.Lock()
        # ``factory_timeout <= 0`` disables the timeout. Catches the
        # "first inbound message → fresh AIAgent ctor hangs forever"
        # stall mode under memory pressure (Hermes 2-instance container,
        # 2026-05-25 field report).
        self._factory_timeout = float(factory_timeout)

    async def _call_factory(self) -> Any:
        if self._factory_timeout > 0:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._factory),
                    timeout=self._factory_timeout,
                )
            except asyncio.TimeoutError as e:
                raise TimeoutError(
                    f"agent_factory did not return within "
                    f"{self._factory_timeout:.1f}s — likely stalled on "
                    f"AIAgent ctor (model load, network, or memory pressure). "
                    f"Increase --factory-timeout if your factory is heavy."
                ) from e
        return await asyncio.to_thread(self._factory)

    async def _get_agent(self, sender_name: str) -> Any:
        if self._pool_size == 0:
            return await self._call_factory()
        async with self._pool_lock:
            if sender_name in self._pool:
                self._pool.move_to_end(sender_name)
                return self._pool[sender_name]
            agent = await self._call_factory()
            self._pool[sender_name] = agent
            if len(self._pool) > self._pool_size:
                self._pool.popitem(last=False)
            return agent

    async def inject(self, msg: InboundMessage) -> Optional[AgentReply]:
        try:
            agent = await self._get_agent(msg.from_name)
        except TimeoutError as e:
            log.error("hermes factory timed out for msg %s: %s", msg.id, e)
            return AgentReply(text="", refusal=str(e))
        prompt = self._build_prompt(msg)
        try:
            chat_fn = getattr(agent, "chat", None)
            if chat_fn is None:
                raise AttributeError(
                    "agent has no .chat method; pass a wrapper if your Hermes "
                    "build uses run_conversation directly"
                )
            result = chat_fn(prompt)
            if asyncio.iscoroutine(result):
                reply_text = await result
            else:
                reply_text = await asyncio.to_thread(lambda: result)
        except Exception as e:
            log.exception("hermes inject failed for msg %s", msg.id)
            return AgentReply(text="", refusal=f"agent error: {e}")
        if not reply_text:
            return None
        return AgentReply(text=str(reply_text).strip())

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

    @staticmethod
    def _build_prompt(msg: InboundMessage) -> str:
        subject = f"\nSubject: {msg.subject}" if msg.subject else ""
        return (
            f"[Incoming ClawTell message from tell/{msg.from_name}]"
            f"{subject}\n\n{msg.body}"
        )
