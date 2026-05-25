"""Adapter contract: every per-framework binding implements this ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ChannelName = Literal["telegram", "discord", "slack", "whatsapp", "signal"]


@dataclass
class InboundMessage:
    id: str
    from_name: str
    subject: Optional[str]
    body: str
    received_at: str
    auto_reply_eligible: bool
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "InboundMessage":
        from_field = d.get("from") or d.get("from_name") or ""
        if from_field.startswith("tell/"):
            from_field = from_field[len("tell/") :]
        return cls(
            id=d.get("id") or d.get("messageId") or "",
            from_name=from_field,
            subject=d.get("subject"),
            body=d.get("body") or d.get("body_decrypted") or "",
            received_at=d.get("created_at")
            or d.get("createdAt")
            or d.get("received_at")
            or "",
            auto_reply_eligible=bool(d.get("auto_reply_eligible", False)),
            raw=d,
        )


@dataclass
class AgentReply:
    text: str
    refusal: Optional[str] = None


@dataclass
class ChatTarget:
    channel: ChannelName
    chat_id: str


@dataclass
class HumanNotification:
    """Either an inbound message alone (auto_reply_eligible=False or always-forward),
    or an inbound plus the agent's reply (auto_reply_eligible=True).
    """

    inbound: InboundMessage
    reply: Optional[AgentReply] = None


class ClawTellAdapter(ABC):
    """Per-framework binding. Implementations override inject() and provide
    a sender callable; everything else has a sensible default."""

    _chat: Optional[ChatTarget] = None
    _directory: dict[str, str] = {}

    @abstractmethod
    async def inject(self, msg: InboundMessage) -> Optional[AgentReply]:
        """Run the agent on the inbound message. Return the reply or None
        if the agent declined to respond."""

    @abstractmethod
    async def forward(
        self, notification: HumanNotification, target: ChatTarget
    ) -> bool:
        """Forward to the human's chat. Default formatting lives in
        clawtell_core.formatter; override only to customize transport."""

    def bind_chat(self, target: ChatTarget) -> None:
        self._chat = target

    def bind_directory(self, directory: dict[str, str]) -> None:
        """{ '<sender_name>': '<chat_id>', '_default': '<chat_id>' }."""
        self._directory = dict(directory)

    def get_chat(self, sender_name: Optional[str] = None) -> Optional[ChatTarget]:
        if sender_name and sender_name in self._directory:
            return ChatTarget(channel="telegram", chat_id=self._directory[sender_name])
        if "_default" in self._directory:
            return ChatTarget(
                channel="telegram", chat_id=self._directory["_default"]
            )
        return self._chat
