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

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("clawtell-telegram")
except PackageNotFoundError:
    __version__ = "0+unknown"
finally:
    try:
        del _pkg_version, PackageNotFoundError
    except NameError:
        pass

__all__ = ["TelegramBridge", "load_persisted_chat"]
