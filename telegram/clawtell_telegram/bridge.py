"""``TelegramBridge`` — capture chat id from first inbound update and
bind it to a ``ClawTellAdapter``.

Long-poll only (no webhook server) so it runs inside any process
without extra infrastructure. Persists to ``~/.clawtell/chat.json`` so
restarts recover the bound chat without waiting for another inbound
update.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from clawtell_core.adapter import ChatTarget, ClawTellAdapter

log = logging.getLogger(__name__)


def _state_path() -> Path:
    base = Path(os.environ.get("CLAWTELL_HOME") or (Path.home() / ".clawtell"))
    return base / "chat.json"


def load_persisted_chat() -> Optional[ChatTarget]:
    """Read the last-bound chat from disk, if any."""
    p = _state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    chat_id = data.get("chat_id")
    channel = data.get("channel", "telegram")
    if not chat_id:
        return None
    return ChatTarget(channel=channel, chat_id=str(chat_id))


def _save_persisted_chat(target: ChatTarget) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"channel": target.channel, "chat_id": target.chat_id}),
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


class TelegramBridge:
    """Long-poll Telegram getUpdates and bind the first chat id seen.

    Usage::

        bridge = TelegramBridge(bot_token=os.environ["TG_BOT_TOKEN"])
        bridge.attach(adapter)               # recovers any persisted chat
        task = asyncio.create_task(bridge.run())   # background poll
        await subscribe(client, adapter)     # main ClawTell loop

    Re-attaching after restart picks up the persisted chat immediately;
    subsequent inbound updates update the bound chat (last sender wins).
    """

    def __init__(
        self,
        bot_token: str,
        *,
        poll_timeout: int = 30,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._token = bot_token
        self._base = f"https://api.telegram.org/bot{bot_token}"
        # Lazy-init the AsyncClient: callers that only use attach() to
        # replay persisted chat should not pay for a network client they
        # never use (and that would otherwise leak under pytest, blocking
        # event-loop teardown).
        self._client: Optional[httpx.AsyncClient] = client
        self._poll_timeout = poll_timeout
        self._adapter: Optional[ClawTellAdapter] = None
        self._offset: Optional[int] = None
        self._stop = asyncio.Event()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._poll_timeout + 5)
        return self._client

    def attach(self, adapter: ClawTellAdapter) -> None:
        """Bind ``adapter`` and replay any persisted chat immediately."""
        self._adapter = adapter
        persisted = load_persisted_chat()
        if persisted:
            adapter.bind_chat(persisted)
            log.info(
                "restored persisted chat %s from %s", persisted.chat_id, _state_path()
            )

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Long-poll loop. Cancel the task or call ``stop()`` to exit."""
        if self._adapter is None:
            raise RuntimeError(
                "TelegramBridge.attach(adapter) must be called before run()"
            )
        log.info("telegram bridge polling for chat capture")
        client = self._ensure_client()
        try:
            while not self._stop.is_set():
                try:
                    updates = await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("telegram getUpdates failed: %s", e)
                    await asyncio.sleep(5)
                    continue
                for u in updates:
                    self._handle_update(u)
        finally:
            await client.aclose()

    async def _poll_once(self) -> list[dict]:
        params: dict[str, Any] = {"timeout": self._poll_timeout}
        if self._offset is not None:
            params["offset"] = self._offset
        client = self._ensure_client()
        r = await client.get(f"{self._base}/getUpdates", params=params)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram getUpdates not ok: %s", data)
            return []
        return data.get("result", []) or []

    def _handle_update(self, update: dict) -> None:
        self._offset = update["update_id"] + 1
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or self._adapter is None:
            return
        target = ChatTarget(channel="telegram", chat_id=str(chat_id))
        self._adapter.bind_chat(target)
        _save_persisted_chat(target)
        log.info("bound telegram chat %s (from update %s)", chat_id, update["update_id"])
