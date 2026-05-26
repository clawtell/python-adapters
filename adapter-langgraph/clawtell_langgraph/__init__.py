"""ClawTell binding adapter for LangGraph.

Per-thread asyncio locks, ``interrupt()`` detection via
``graph.aget_state()``, branch invoke/resume. Pairs with
``clawtell_core.subscribe`` and a Telegram sender.
"""

from clawtell_langgraph.adapter import LangGraphAdapter
from clawtell_langgraph.tools import make_clawtell_send_tool

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("clawtell-langgraph")
except PackageNotFoundError:
    __version__ = "0+unknown"
finally:
    try:
        del _pkg_version, PackageNotFoundError
    except NameError:
        pass

__all__ = ["LangGraphAdapter", "make_clawtell_send_tool"]
