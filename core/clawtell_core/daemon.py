"""``clawtell-forwarder`` console entry point.

Default mode (no subcommand) runs ``subscribe(client, adapter)`` as a
long-lived process. Three onboarding subcommands also live here:

    clawtell-forwarder check         # validate config end-to-end
    clawtell-forwarder discover-chat # capture chat_id from first inbound Telegram update
    clawtell-forwarder send-test     # send a "ClawTell connected" test message

Usage (forwarder)::

    clawtell-forwarder \\
        --adapter clawtell_hermes:HermesAdapter \\
        --agent-factory my_agent:make_agent \\
        --telegram-token-env TG_BOT_TOKEN

Or the "just forward to Telegram, no agent reply" path::

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


async def _load_dotted_with_timeout(spec: str, timeout: float, kind: str) -> Any:
    """Import a dotted spec under a wall-clock timeout. Catches module-level
    side effects (model load, network call, etc.) that would otherwise hang
    the daemon at startup before ``subscribe()`` is ever called."""
    if timeout <= 0:
        return _load_dotted(spec)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_load_dotted, spec),
            timeout=timeout,
        )
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            f"{kind} {spec!r} did not import within {timeout:.1f}s — "
            f"likely module-level side effects (model load, network, "
            f"heavy ctor at import). Increase --factory-timeout or move "
            f"side effects into the factory body."
        ) from e


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


def _directory_path() -> Path:
    return Path(
        os.environ.get("CLAWTELL_CHANNEL_DIRECTORY")
        or (Path.home() / ".clawtell" / "channel-directory.json")
    )


def _load_directory() -> dict[str, str]:
    path = _directory_path()
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


def _log_boot_config(args: argparse.Namespace, tg_token: bool) -> None:
    """One line at boot listing every effective config knob. Half of all
    "why isn't it working" questions are answered by reading this line."""
    if args.adapter:
        mode = "full"
    elif args.forward_only:
        mode = "forward-only"
    else:
        # Current behavior: implicit forward-only if no --adapter. Make
        # the inferred mode visible so users don't wonder later.
        mode = "forward-only (inferred — pass --forward-only to be explicit)"
    parts = [
        f"mode={mode}",
        f"adapter={args.adapter or '-'}",
        f"agent_factory={args.agent_factory or '-'}",
        f"graph_factory={args.graph_factory or '-'}",
        f"default_chat={args.default_chat or '-'}",
        f"factory_timeout={args.factory_timeout:.0f}s",
        f"heartbeat_file={args.heartbeat_file or '-'}",
        f"telegram_token={'set' if tg_token else 'missing'}",
        f"channel_directory={_directory_path()}",
    ]
    log.info("boot config: %s", " ".join(parts))


def _deployment_shape_advisory(args: argparse.Namespace) -> None:
    """Warn when the user is likely running a second AIAgent in a container
    that already has one (e.g. OpenClaw gateway). Two AIAgent ctors in one
    memory budget is the most common stall mode. ``--allow-collocated-agent``
    silences this when the user has consciously sized the host."""
    if args.adapter and not args.allow_collocated_agent:
        log.warning(
            "deployment-shape: running --adapter (full mode) — if another "
            "AIAgent already runs in this container/host (e.g. an OpenClaw "
            "gateway), each inbound spins up a *second* AIAgent ctor and "
            "you will hit memory pressure or factory_timeout. Use "
            "--forward-only when an agent is already collocated, or pass "
            "--allow-collocated-agent to silence this warning. See: "
            "https://github.com/clawtell/python-adapters/blob/main/core/README.md#deployment-shapes"
        )


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
    _log_boot_config(args, bool(tg_token))
    _deployment_shape_advisory(args)
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
        try:
            adapter_cls = await _load_dotted_with_timeout(
                args.adapter, args.factory_timeout, "adapter"
            )
        except TimeoutError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        kwargs: dict = {"sender": sender}
        # Forward the timeout to adapters that accept it (HermesAdapter
        # uses it for the per-message factory call). Adapters that don't
        # take the kwarg ignore it via the explicit branch below.
        if args.agent_factory:
            try:
                kwargs["agent_factory"] = await _load_dotted_with_timeout(
                    args.agent_factory, args.factory_timeout, "agent factory"
                )
            except TimeoutError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            kwargs["factory_timeout"] = args.factory_timeout
        if args.graph_factory:
            # LangGraph-style: invoke the factory once and pass the
            # compiled graph as `graph=`. Hermes-style uses agent_factory
            # because it instantiates one agent per message.
            try:
                graph_factory = await _load_dotted_with_timeout(
                    args.graph_factory, args.factory_timeout, "graph factory"
                )
                kwargs["graph"] = await asyncio.wait_for(
                    asyncio.to_thread(graph_factory),
                    timeout=args.factory_timeout,
                ) if args.factory_timeout > 0 else graph_factory()
            except (TimeoutError, asyncio.TimeoutError) as e:
                print(
                    f"error: graph factory {args.graph_factory!r} did not "
                    f"build within {args.factory_timeout:.1f}s: {e}",
                    file=sys.stderr,
                )
                return 2
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

    heartbeat = Path(args.heartbeat_file) if args.heartbeat_file else None
    try:
        await subscribe(
            client, adapter, stop_event=stop, heartbeat_file=heartbeat
        )
    finally:
        if sender_close is not None:
            await sender_close.aclose()
    return 0


# ───────────────────────────── subcommands ─────────────────────────────


async def _cmd_check(args: argparse.Namespace) -> int:
    """Preflight: validate API key, ping API, validate Telegram token,
    validate chat binding. Exits 0 with green checks or non-zero with a
    precise diagnostic so misconfig dies loud at install time, not
    silently at runtime."""
    ok = True

    def line(symbol: str, label: str, detail: str = "") -> None:
        suffix = f"  ({detail})" if detail else ""
        print(f"  {symbol} {label}{suffix}")

    print("clawtell-forwarder check")
    print("=" * 60)

    creds = load_credentials(api_key=args.api_key, name=args.name)
    if not creds.api_key:
        line(
            "x",
            "CLAWTELL_API_KEY",
            "not found in env, ~/.config/clawtell.env, ~/.clawtell/credentials.env",
        )
        return 1
    line("✓", "CLAWTELL_API_KEY", f"loaded ({len(creds.api_key)} chars)")

    try:
        from clawtell import ClawTell

        client = ClawTell(api_key=creds.api_key)
        me = await asyncio.to_thread(client.me)
        line(
            "✓",
            "ClawTell API auth",
            f"agent tell/{me.get('name', '?')}",
        )
    except Exception as e:
        line("x", "ClawTell API auth", str(e))
        return 1

    tg_token = _discover_telegram_token(args.telegram_token_env or "TG_BOT_TOKEN")
    if not tg_token:
        line(
            "x",
            "Telegram bot token",
            f"not found in env ({args.telegram_token_env}, TELEGRAM_BOT_TOKEN, TG_BOT_TOKEN)",
        )
        ok = False
    else:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(f"https://api.telegram.org/bot{tg_token}/getMe")
            if r.status_code == 200 and r.json().get("ok"):
                bot = r.json()["result"]
                line(
                    "✓",
                    "Telegram bot token",
                    f"@{bot.get('username', '?')}",
                )
            else:
                line(
                    "x",
                    "Telegram bot token",
                    f"getMe returned {r.status_code} — token invalid or revoked",
                )
                ok = False
        except Exception as e:
            line("x", "Telegram bot token", f"network error: {e}")
            ok = False

    directory = _load_directory()
    if directory:
        default_chat = directory.get("_default")
        line(
            "✓",
            "Channel directory",
            f"{len(directory)} entries, _default={default_chat or 'none'}",
        )
    elif args.default_chat:
        line(
            "✓",
            "Channel binding",
            f"--default-chat={args.default_chat}",
        )
    else:
        line(
            "!",
            "Channel binding",
            "no directory file and no --default-chat — daemon will queue "
            "all inbounds until you bind a chat. Run 'clawtell-forwarder "
            "discover-chat' to capture one.",
        )

    print("=" * 60)
    if ok:
        print("OK — config looks healthy")
        return 0
    print("FAIL — fix the items marked x above")
    return 1


async def _cmd_discover_chat(args: argparse.Namespace) -> int:
    """Listen for the first inbound Telegram update, print the chat_id,
    optionally write it to ~/.clawtell/channel-directory.json. Removes
    the chat-ID hunt that bites every new user."""
    tg_token = _discover_telegram_token(args.telegram_token_env or "TG_BOT_TOKEN")
    if not tg_token:
        print(
            f"error: no Telegram bot token found in env "
            f"({args.telegram_token_env}, TELEGRAM_BOT_TOKEN, TG_BOT_TOKEN)",
            file=sys.stderr,
        )
        return 2

    print(
        "Send any message to your Telegram bot now. Waiting up to "
        f"{args.timeout}s..."
    )
    import httpx

    base = f"https://api.telegram.org/bot{tg_token}"
    deadline = asyncio.get_event_loop().time() + args.timeout
    offset: Optional[int] = None
    async with httpx.AsyncClient(timeout=35.0) as http:
        while asyncio.get_event_loop().time() < deadline:
            params: dict = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            try:
                r = await http.get(f"{base}/getUpdates", params=params)
                payload = r.json()
            except Exception as e:
                print(f"poll error: {e}", file=sys.stderr)
                await asyncio.sleep(2)
                continue
            for update in payload.get("result", []) or []:
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                if chat_id is None:
                    continue
                chat_id_str = str(chat_id)
                title = (
                    chat.get("title")
                    or chat.get("username")
                    or chat.get("first_name")
                    or "?"
                )
                kind = chat.get("type", "?")
                print()
                print(f"chat_id: {chat_id_str}")
                print(f"name:    {title}")
                print(f"type:    {kind}")
                if args.write:
                    return _write_directory(chat_id_str, title)
                print()
                print("To bind this chat:")
                print(f"  clawtell-forwarder --default-chat {chat_id_str} ...")
                print("Or rerun with --write to persist to "
                      "~/.clawtell/channel-directory.json")
                return 0
    print("timeout — no Telegram update received", file=sys.stderr)
    return 1


def _write_directory(chat_id: str, label: str) -> int:
    path = _directory_path()
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing["_default"] = chat_id
    safe_label = "".join(c for c in label if c.isalnum() or c in "-_") or "user"
    existing[safe_label] = chat_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print()
    print(f"wrote {path}")
    print(f"  _default = {chat_id}")
    print(f"  {safe_label} = {chat_id}")
    return 0


async def _cmd_send_test(args: argparse.Namespace) -> int:
    """End-to-end self-test: send a "ClawTell connected" message via the
    configured Telegram sender to the bound chat. Confirms the full pipe
    (auth → token → chat → Telegram) before any real traffic flows."""
    tg_token = _discover_telegram_token(args.telegram_token_env or "TG_BOT_TOKEN")
    if not tg_token:
        print(
            f"error: no Telegram bot token found in env "
            f"({args.telegram_token_env}, TELEGRAM_BOT_TOKEN, TG_BOT_TOKEN)",
            file=sys.stderr,
        )
        return 2

    if args.default_chat:
        chat_id = args.default_chat
        source = "--default-chat"
    else:
        directory = _load_directory()
        if args.to and args.to in directory:
            chat_id = directory[args.to]
            source = f"directory[{args.to}]"
        elif "_default" in directory:
            chat_id = directory["_default"]
            source = "directory[_default]"
        else:
            print(
                "error: no chat binding — pass --default-chat <id>, "
                "--to <name>, or populate ~/.clawtell/channel-directory.json",
                file=sys.stderr,
            )
            return 2

    import httpx

    text = (
        "ClawTell connected — this is a test message from "
        "`clawtell-forwarder send-test`. If you see this, your bot token, "
        "chat binding, and network egress are all working."
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.post(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
    if r.status_code == 200 and r.json().get("ok"):
        print(f"OK — test message delivered to chat {chat_id} ({source})")
        return 0
    print(
        f"FAIL — Telegram returned {r.status_code}: {r.text}", file=sys.stderr
    )
    return 1


# ─────────────────────────────── entry ────────────────────────────────


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--api-key", help="override CLAWTELL_API_KEY")
    p.add_argument("--name", help="override CLAWTELL_NAME")
    p.add_argument(
        "--telegram-token-env",
        default="TG_BOT_TOKEN",
        help="env var holding the Telegram bot token",
    )
    p.add_argument(
        "--log-level",
        default=os.environ.get("CLAWTELL_LOG_LEVEL", "INFO"),
        help="DEBUG / INFO / WARNING / ERROR",
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="clawtell-forwarder")

    # ── forwarder flags on the MAIN parser so the no-subcommand mode
    #    (existing usage) keeps working without changes. Subparsers
    #    below add their own flags for the onboarding subcommands.
    p.add_argument("--adapter", help="dotted path to adapter class, e.g. clawtell_hermes:HermesAdapter")
    p.add_argument("--agent-factory", help="dotted path to zero-arg factory returning a framework agent instance (Hermes-style — called per message)")
    p.add_argument("--graph-factory", help="dotted path to zero-arg factory returning a compiled graph (LangGraph-style — called once at startup)")
    p.add_argument("--forward-only", action="store_true", help="no agent; just forward inbound to Telegram")
    p.add_argument("--default-chat", help="Telegram chat_id fallback if no directory")
    p.add_argument(
        "--factory-timeout",
        type=float,
        default=float(os.environ.get("CLAWTELL_FACTORY_TIMEOUT", "30") or 30),
        help=(
            "seconds to wait for adapter/agent_factory/graph_factory imports "
            "and for per-message AIAgent construction before giving up. "
            "Catches model-load / memory-pressure stalls. 0 disables. "
            "Default 30 (env: CLAWTELL_FACTORY_TIMEOUT)."
        ),
    )
    p.add_argument(
        "--heartbeat-file",
        default=os.environ.get("CLAWTELL_HEARTBEAT_FILE"),
        help=(
            "path touched at every progress checkpoint (start of poll, "
            "after each ack, after each send, before sleep). Use with an "
            "external watchdog (systemd, k8s liveness, sidecar cron) to "
            "detect hung-but-alive. Off by default. "
            "Env: CLAWTELL_HEARTBEAT_FILE."
        ),
    )
    p.add_argument(
        "--allow-collocated-agent",
        action="store_true",
        help=(
            "silence the deployment-shape warning when running --adapter "
            "(full mode) on a host that already has another AIAgent (e.g. "
            "OpenClaw gateway). Only pass this once you've sized the host "
            "for two AIAgent instances."
        ),
    )
    _add_common_flags(p)

    sub = p.add_subparsers(dest="subcommand", required=False)

    check_p = sub.add_parser(
        "check",
        help="validate config end-to-end (API auth, Telegram token, chat binding)",
    )
    _add_common_flags(check_p)
    check_p.add_argument("--default-chat", help="Telegram chat_id to validate")

    disc_p = sub.add_parser(
        "discover-chat",
        help="capture chat_id from the first inbound Telegram update",
    )
    _add_common_flags(disc_p)
    disc_p.add_argument(
        "--write",
        action="store_true",
        help="persist the captured chat_id to ~/.clawtell/channel-directory.json",
    )
    disc_p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="seconds to wait for an inbound (default 300)",
    )

    sent_p = sub.add_parser(
        "send-test",
        help='send a "ClawTell connected" test message end-to-end',
    )
    _add_common_flags(sent_p)
    sent_p.add_argument("--to", help="sender name from channel-directory.json")
    sent_p.add_argument(
        "--default-chat",
        help="Telegram chat_id to send to (overrides directory)",
    )

    args = p.parse_args()

    logging.basicConfig(
        level=str(getattr(args, "log_level", "INFO")).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.subcommand == "check":
        sys.exit(asyncio.run(_cmd_check(args)))
    if args.subcommand == "discover-chat":
        sys.exit(asyncio.run(_cmd_discover_chat(args)))
    if args.subcommand == "send-test":
        sys.exit(asyncio.run(_cmd_send_test(args)))

    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
