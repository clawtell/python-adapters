"""ClawTell framework-agnostic adapter core.

Pairs with clawtell-hermes, clawtell-langgraph, etc. — the per-framework
binding packages. This package owns the polling loop, disk queue,
allowlist gate, and the default lobster-banner formatting.
"""

from clawtell_core.adapter import (
    AgentReply,
    ChatTarget,
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)
from clawtell_core.config import discover_env_var, load_credentials
from clawtell_core.formatter import format_inbound, format_reply
from clawtell_core.subscribe import subscribe

__version__ = "2026.5.25"

__all__ = [
    "AgentReply",
    "ChatTarget",
    "ClawTellAdapter",
    "HumanNotification",
    "InboundMessage",
    "discover_env_var",
    "format_inbound",
    "format_reply",
    "load_credentials",
    "subscribe",
]
