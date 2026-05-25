"""Telegram auto-binder for ClawTell.

``TelegramBridge`` long-polls Telegram's getUpdates endpoint and
captures the first chat id it sees, persisting it to
``~/.clawtell/chat.json`` and binding it to a ``ClawTellAdapter``.

For multi-recipient setups, the channel directory at
``~/.clawtell/channel-directory.json`` maps ``{ sender_name: chat_id,
_default: chat_id }`` and is loaded by the daemon directly (see
``clawtell_core.daemon``); the bridge below is for the
single-active-chat case.
"""

from clawtell_telegram.bridge import TelegramBridge, load_persisted_chat

__version__ = "2026.5.25"
__all__ = ["TelegramBridge", "load_persisted_chat"]
