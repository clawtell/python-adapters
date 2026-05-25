"""``clawtell-forwarder`` console entry point.

Runs ``subscribe(client, adapter)`` as a long-lived process. Adapter is
loaded by dotted path so users don't write their own daemonizer.

Usage::

    clawtell-forwarder \\
        --adapter clawtell_hermes:HermesAdapter \\
        --agent-factory my_agent:make_agent \\
        --telegram-token-env TG_BOT_TOKEN

Or simpler — for the "just forward to Telegram, no agent reply" case
(matches what the user built by hand on Hermes)::

    clawtell-forwarder --forward-only --telegram-token-env TG_BOT_TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Optional

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

log = logging.getLogger("clawtell.forwarder")


def _load_dotted(spec: str) -> Any:
    """``module.submodule:attr`` → loaded attr."""
    if ":" not in spec:
        raise ValueError(f"expected 'module:attr', got {spec!r}")
    mod_name, attr = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def _normalize_directory(data: object) -> dict[str, str]:
    """Accept either the flat ClawTell shape or the OpenClaw nested shape.

    Flat (documented)::

        {"alice": "111", "_default": "222"}

    OpenClaw nested (what hand-rolled forwarders that mimic OpenClaw's
    ``sessions.json`` / ``channel_directory.json`` tend to produce)::

        {
            "updated_at": "...",
            "platforms": {
                "telegram": [
                    {"id": "111", "name": "alice", "type": "private"},
                    ...
                ]
            }
        }

    The nested shape is auto-flattened: the first telegram entry becomes
    ``_default``; entries with a ``name`` also map by name. This lets
    users migrate from an OpenClaw-style file without re-shaping it.
    """
    if not isinstance(data, dict):
        return {}
    platforms = data.get("platforms")
    if isinstance(platforms, dict):
        result: dict[str, str] = {}
        telegram = platforms.get("telegram") or []
        if isinstance(telegram, list):
            for entry in telegram:
                if not isinstance(entry, dict):
                    continue
                chat_id = entry.get("id") or entry.get("chat_id")
                if chat_id is None:
                    continue
                chat_id_str = str(chat_id)
                if "_default" not in result:
                    result["_default"] = chat_id_str
                name = entry.get("name") or entry.get("sender_name")
                if name:
                    result[str(name)] = chat_id_str
        return result
    # Flat shape: keep only scalar values, coerce to str.
    return {
        str(k): str(v)
        for k, v in data.items()
        if not isinstance(v, (dict, list))
    }


def _load_directory() -> dict[str, str]:
    path = Path(
        os.environ.get("CLAWTELL_CHANNEL_DIRECTORY")
        or (Path.home() / ".clawtell" / "channel-directory.json")
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read channel directory at %s: %s", path, e)
        return {}
    return _normalize_directory(data)


class _ForwardOnlyAdapter(ClawTellAdapter):
    """Adapter that NEVER auto-replies — just shows inbound messages to the
    human chat. Equivalent to the user's hand-rolled forwarder."""

    def __init__(self, sender):
        self._sender = sender

    async def inject(self, msg: InboundMessage) -> Optional[AgentReply]:
        # Force "not eligible" semantics by returning None; subscribe()
        # treats this as "no reply produced".
        return None

    async def forward(
        self, notification: HumanNotification, target: ChatTarget
    ) -> bool:
        if notification.reply:
            text = format_reply(notification.reply, notification.inbound)
        else:
            text = format_inbound(notification.inbound)
        await self._sender(target.chat_id, text)
        return True


async def _telegram_sender_factory(bot_token: str):
    import httpx

    base = f"https://api.telegram.org/bot{bot_token}"
    client = httpx.AsyncClient(timeout=15.0)

    async def send(chat_id: str, text: str) -> None:
        # Plain text — body content may contain markdown/HTML characters
        # that would otherwise need escaping. Telegram accepts text as-is.
        r = await client.post(
            f"{base}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        if r.status_code >= 400:
            log.error("telegram send %s: %s", r.status_code, r.text)

    return send, client


def _discover_telegram_token(env_name: str) -> Optional[str]:
    """Try the user-specified env var, then common aliases. On linux,
    fall back to /proc/self/environ then /proc/1/environ so the daemon
    can inherit a token loaded by the gateway / init process."""
    for candidate in (
        env_name,
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_API_KEY",
        "TG_BOT_TOKEN",
    ):
        v = discover_env_var(candidate)
        if v:
            return v
    return None


async def _amain(args: argparse.Namespace) -> int:
    creds = load_credentials(api_key=args.api_key, name=args.name)
    if not creds.api_key:
        print(
            "error: no CLAWTELL_API_KEY found "
            "(env, ~/.config/clawtell.env, ~/.clawtell/credentials.env)",
            file=sys.stderr,
        )
        return 2

    from clawtell import ClawTell

    client = ClawTell(api_key=creds.api_key)

    tg_token = _discover_telegram_token(args.telegram_token_env or "TG_BOT_TOKEN")
    sender = None
    sender_close = None
    if tg_token:
        sender, sender_close = await _telegram_sender_factory(tg_token)
        log.info("telegram sender configured")
    else:
        log.warning(
            "no Telegram bot token found (checked env + /proc/self/environ + /proc/1/environ)"
        )

    if args.adapter:
        if not sender:
            print(
                "error: --adapter requires a Telegram sender — set "
                "TG_BOT_TOKEN (or --telegram-token-env) to a token the "
                "process can read (env / procfs).",
                file=sys.stderr,
            )
            return 2
        adapter_cls = _load_dotted(args.adapter)
        kwargs: dict = {"sender": sender}
        if args.agent_factory:
            kwargs["agent_factory"] = _load_dotted(args.agent_factory)
        if args.graph_factory:
            # LangGraph-style: invoke the factory once and pass the
            # compiled graph as `graph=`. Hermes-style uses agent_factory
            # because it instantiates one agent per message.
            kwargs["graph"] = _load_dotted(args.graph_factory)()
        adapter: ClawTellAdapter = adapter_cls(**kwargs)
    else:
        if not sender:
            print(
                "error: --forward-only requires a Telegram sender "
                "(set TG_BOT_TOKEN or --telegram-token-env)",
                file=sys.stderr,
            )
            return 2
        adapter = _ForwardOnlyAdapter(sender=sender)

    directory = _load_directory()
    if directory:
        adapter.bind_directory(directory)
        log.info("loaded channel directory with %d entries", len(directory))
    elif args.default_chat:
        adapter.bind_chat(ChatTarget(channel="telegram", chat_id=args.default_chat))

    stop = asyncio.Event()

    def _shutdown(*_):
        log.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, _shutdown)  # windows

    try:
        await subscribe(client, adapter, stop_event=stop)
    finally:
        if sender_close is not None:
            await sender_close.aclose()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="clawtell-forwarder")
    p.add_argument("--adapter", help="dotted path to adapter class, e.g. clawtell_hermes:HermesAdapter")
    p.add_argument("--agent-factory", help="dotted path to zero-arg factory returning a framework agent instance (Hermes-style — called per message)")
    p.add_argument("--graph-factory", help="dotted path to zero-arg factory returning a compiled graph (LangGraph-style — called once at startup)")
    p.add_argument("--forward-only", action="store_true", help="no agent; just forward inbound to Telegram")
    p.add_argument("--telegram-token-env", default="TG_BOT_TOKEN", help="env var holding the Telegram bot token")
    p.add_argument("--default-chat", help="Telegram chat_id fallback if no directory")
    p.add_argument("--api-key", help="override CLAWTELL_API_KEY")
    p.add_argument("--name", help="override CLAWTELL_NAME")
    p.add_argument(
        "--log-level",
        default=os.environ.get("CLAWTELL_LOG_LEVEL", "INFO"),
        help="DEBUG / INFO / WARNING / ERROR",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
