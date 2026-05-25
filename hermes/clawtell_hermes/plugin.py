"""Hermes plugin that registers a ``clawtell_send`` agent tool.

Lets the agent initiate ClawTell sends (not just reply). Mirrors the
OpenClaw plugin's native tool — Hermes-flavored.

Usage in user's plugin entry point::

    from clawtell_hermes.plugin import register as register_clawtell

    def register(ctx):
        register_clawtell(ctx)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from clawtell_core.config import load_credentials

log = logging.getLogger(__name__)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {
            "type": "string",
            "description": "Recipient name (with or without tell/ prefix).",
        },
        "body": {"type": "string", "description": "Message body."},
        "subject": {"type": "string"},
        "reply_to_id": {
            "type": "string",
            "description": "Optional message id to thread the reply.",
        },
    },
    "required": ["to", "body"],
}


def _make_handler(client: Any):
    def handler(args: dict) -> str:
        to = args["to"]
        if to.startswith("tell/"):
            to = to[len("tell/") :]
        try:
            result = client.send(
                to=to,
                body=args["body"],
                subject=args.get("subject"),
            )
        except Exception as e:
            return f"× Send failed: {e}"
        status = result.get("status")
        if status == "pending_approval":
            return f"… Pending approval — tell/{to} will receive once approved"
        return f"✓ Sent to tell/{to}"

    return handler


def register(ctx: Any, *, client: Any = None) -> None:
    """Hermes plugin hook. ``ctx.register_tool(...)`` is the only Hermes
    API touched. If ``client`` is omitted, build one from environment via
    ``clawtell_core.load_credentials``."""
    if client is None:
        from clawtell import ClawTell

        creds = load_credentials()
        if not creds.api_key:
            log.warning(
                "clawtell_send tool not registered: no CLAWTELL_API_KEY found"
            )
            return
        client = ClawTell(api_key=creds.api_key)

    ctx.register_tool(
        name="clawtell_send",
        toolset=os.environ.get("CLAWTELL_HERMES_TOOLSET", "communication"),
        schema=_TOOL_SCHEMA,
        handler=_make_handler(client),
        description=(
            "Send a message to another ClawTell agent (tell/<name>). "
            "Returns one of three verbatim strings — relay to the human "
            "exactly: '✓ Sent to tell/<name>' (immediate), "
            "'… Pending approval — tell/<name> will receive once approved' "
            "(needs recipient owner approval), '× Send failed: <reason>'."
        ),
    )
    log.info("clawtell_send Hermes tool registered")
