"""ClawTell binding adapter for LangGraph.

Per-thread asyncio locks, ``interrupt()`` detection via
``graph.aget_state()``, branch invoke/resume. Pairs with
``clawtell_core.subscribe`` and a Telegram sender.
"""

from clawtell_langgraph.adapter import LangGraphAdapter
from clawtell_langgraph.tools import make_clawtell_send_tool

__version__ = "2026.5.25"
__all__ = ["LangGraphAdapter", "make_clawtell_send_tool"]
