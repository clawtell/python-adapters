"""Default lobster-banner formatting for human-chat notifications.

Adapters can override forward() entirely; if they call format_inbound() /
format_reply(), they get the brand-consistent template that matches the
OpenClaw plugin's rendering.
"""

from __future__ import annotations

from clawtell_core.adapter import AgentReply, InboundMessage

BANNER = "🦞🦞 ClawTell Delivery 🦞🦞"


def _attachments_line(msg: InboundMessage) -> str:
    atts = msg.raw.get("attachments") if isinstance(msg.raw, dict) else None
    if not atts:
        return ""
    names = []
    for a in atts:
        if isinstance(a, dict):
            names.append(a.get("filename") or a.get("name") or a.get("id") or "(file)")
        else:
            names.append(str(a))
    return f"\nAttachments: {', '.join(names)}"


def format_inbound(msg: InboundMessage) -> str:
    """Render an incoming message for the human chat (no agent reply yet).

    Matches the OpenClaw channel plugin's lobster-banner template
    (channel/src/poll.ts) — banner, ``From: tell/<sender>``, optional
    ``Subject:``, blank line, body. Receive timestamp is visible in the
    Telegram bubble itself, and ``auto_reply_eligible`` is daemon-log
    metadata, not human-chat content.

    Plain text — no parse_mode required at the Telegram send site. Keep
    body content as-is so users don't have to worry about escaping
    markdown / HTML inside their messages.
    """
    subject_line = f"\nSubject: {msg.subject}" if msg.subject else ""
    return (
        f"{BANNER}\n"
        f"From: tell/{msg.from_name}{subject_line}\n\n"
        f"{msg.body}"
        f"{_attachments_line(msg)}"
    )


def format_reply(reply: AgentReply, msg: InboundMessage) -> str:
    """Render an inbound message and the agent's reply together — what the
    human sees after the agent auto-responded."""
    subject_line = f"\nSubject: {msg.subject}" if msg.subject else ""
    if reply.refusal:
        reply_block = f"(declined to reply: {reply.refusal})"
    else:
        reply_block = f"→ Replied:\n{reply.text}"
    return (
        f"{BANNER}\n"
        f"From: tell/{msg.from_name}{subject_line}\n\n"
        f"{msg.body}\n\n"
        f"{reply_block}"
        f"{_attachments_line(msg)}"
    )
