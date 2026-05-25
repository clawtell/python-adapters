"""Defensive local auto-reply gate.

The ClawTell server is the primary authority — every poll() message
carries ``auto_reply_eligible`` already-decided by the recipient's policy.
This module exists for the unusual case where the user wants to layer
*tighter* local restrictions on top (e.g. test environments, or a deny-by-
default config the agent enforces regardless of server policy).

Ported from ``Repos/channel/src/poll.ts:215-234`` (``isAutoReplyAllowed``).
"""

from __future__ import annotations

from typing import Literal, Optional

Mode = Literal["everyone", "allowlist_only", "manual_only"]


def is_auto_reply_allowed(
    sender: str,
    *,
    mode: Optional[Mode] = None,
    allowlist: Optional[list[str]] = None,
) -> bool:
    """Local gate. Fail-closed: if no allowlist is configured and mode is
    not explicitly ``everyone``, return False.

    Args:
        sender: tell/<name> or just <name>; case-insensitive.
        mode: one of "everyone", "allowlist_only", "manual_only". If None,
            inferred as "allowlist_only" when allowlist is non-empty, else
            "manual_only".
        allowlist: list of names (with or without ``tell/`` prefix).
    """
    allowlist = allowlist or []
    if mode is None:
        mode = "allowlist_only" if allowlist else "manual_only"

    if mode == "manual_only":
        return False
    if mode == "everyone":
        return True

    if not allowlist:
        return False
    norm = sender.lower()
    if norm.startswith("tell/"):
        norm = norm[len("tell/") :]
    return norm in {
        a.lower()[len("tell/") :] if a.lower().startswith("tell/") else a.lower()
        for a in allowlist
        if a
    }
