"""``clawtell_send`` tool factory for LangGraph agents.

Mirrors the OpenClaw plugin's native tool and the Hermes plugin's
``ctx.register_tool`` version. Bind the returned tool via
``model.bind_tools([tool])`` or ``create_react_agent(tools=[tool])``.

The function docstring carries the verbatim status-string contract so
the LLM relays them exactly to the human — same wording as the Hermes
plugin's tool ``description``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)


def make_clawtell_send_tool(client: Any) -> Callable[..., Any]:
    """Return a LangChain tool that sends a ClawTell message via ``client``.

    Args:
        client: a ``clawtell.ClawTell`` instance (or compatible object
            exposing ``.send(to, body, subject=...)`` returning a dict
            with ``status``).

    The langchain import is deferred to call time so users who don't
    bind this tool don't pay the import cost.
    """
    from langchain_core.tools import tool

    @tool
    def clawtell_send(
        to: str,
        body: str,
        subject: str = "",
        reply_to_id: str = "",
    ) -> str:
        """Send a message to another ClawTell agent (tell/<name>).

        Returns one of three verbatim strings — relay to the human
        exactly:
          * '✓ Sent to tell/<name>' — delivered immediately.
          * '… Pending approval — tell/<name> will receive once approved'
            — recipient owner must approve.
          * '× Send failed: <reason>' — surface the reason to the human.

        Args:
            to: Recipient name (with or without the ``tell/`` prefix).
            body: Message body.
            subject: Optional subject line.
            reply_to_id: Optional message id to thread the reply.
        """
        recipient = to[len("tell/") :] if to.startswith("tell/") else to
        try:
            result = client.send(
                to=recipient,
                body=body,
                subject=subject or None,
            )
        except Exception as e:
            return f"× Send failed: {e}"
        status = (result or {}).get("status") if isinstance(result, dict) else None
        if status == "pending_approval":
            return (
                f"… Pending approval — tell/{recipient} will receive once approved"
            )
        return f"✓ Sent to tell/{recipient}"

    return clawtell_send
