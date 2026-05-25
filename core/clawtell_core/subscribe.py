"""The framework-agnostic ClawTell receive loop.

Three-branch dispatch per inbound message:

  1. No chat bound for this sender    → enqueue, do NOT ack
                                        (server redelivers after window)
  2. ``auto_reply_eligible: false``   → forward inbound to human chat, ack,
                                        do NOT invoke agent
  3. ``auto_reply_eligible: true``    → invoke agent, forward both inbound
                                        and reply, send reply via ClawTell,
                                        ack
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from clawtell_core.adapter import (
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)
from clawtell_core.queue import InboxQueue, QueuedMessage

log = logging.getLogger(__name__)


DEDUP_WINDOW = 500


def _touch_heartbeat(path: Optional[Path]) -> None:
    """Update mtime of the heartbeat file so external watchdogs can detect
    a hung-but-alive forwarder. Called at every progress checkpoint inside
    the receive loop — start of poll, after each ack, after each send,
    before sleep — so "stale heartbeat" means the loop has genuinely
    stopped making progress, not just that we're mid-message."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``Path.touch`` updates mtime if exists, else creates.
        path.touch(exist_ok=True)
        os.utime(path, None)
    except OSError as e:
        log.warning("heartbeat write failed (%s): %s", path, e)


class _DedupWindow:
    """Rolling window of recently-processed message IDs — safety net for
    redeliveries when ack() races with a crash. Lost on restart, which is
    fine: the server's ack TTL handles longer windows."""

    def __init__(self, size: int = DEDUP_WINDOW) -> None:
        self._size = size
        self._order: list[str] = []
        self._set: set[str] = set()

    def seen(self, msg_id: str) -> bool:
        return msg_id in self._set

    def add(self, msg_id: str) -> None:
        if msg_id in self._set:
            return
        self._set.add(msg_id)
        self._order.append(msg_id)
        if len(self._order) > self._size:
            evict = self._order.pop(0)
            self._set.discard(evict)


async def subscribe(
    client: Any,
    adapter: ClawTellAdapter,
    *,
    poll_timeout: int = 30,
    poll_limit: int = 50,
    drain_interval: float = 60.0,
    empty_poll_sleep: float = 1.0,
    stop_event: Optional[asyncio.Event] = None,
    heartbeat_file: Optional[Path] = None,
) -> None:
    """Run the receive loop until ``stop_event`` is set (or forever).

    Args:
        client: a ``clawtell.ClawTell`` (or duck-typed) instance with
            ``poll``, ``ack``, ``send``.
        adapter: a ``ClawTellAdapter`` subclass instance.
        poll_timeout: long-poll seconds passed through to ``client.poll``.
        poll_limit: max messages per poll.
        drain_interval: seconds between queue-drain attempts.
        stop_event: optional asyncio.Event to break the loop.
        heartbeat_file: optional path touched at every progress checkpoint.
            External watchdogs (systemd, k8s liveness, sidecar cron) can
            detect hung-but-alive by comparing mtime to a stale threshold.
    """
    queue = InboxQueue()
    dedup = _DedupWindow()
    last_drain = 0.0
    log.info("subscribe loop starting (timeout=%ds, limit=%d)", poll_timeout, poll_limit)
    _touch_heartbeat(heartbeat_file)

    while not (stop_event and stop_event.is_set()):
        _touch_heartbeat(heartbeat_file)
        try:
            res = await asyncio.to_thread(
                client.poll, timeout=poll_timeout, limit=poll_limit
            )
        except Exception as e:  # transient HTTP errors etc.
            log.warning("poll error: %s; sleeping 5s", e)
            await asyncio.sleep(5)
            continue

        messages = res.get("messages", []) or []
        for raw in messages:
            msg = InboundMessage.from_dict(raw)
            if dedup.seen(msg.id):
                log.debug("skipping duplicate msg %s", msg.id)
                # Re-ack defensively in case the prior ack didn't land.
                try:
                    await asyncio.to_thread(client.ack, [msg.id])
                except Exception:
                    pass
                continue
            # Only mark seen when we've TAKEN RESPONSIBILITY for the
            # message (acked, or persisted to the queue). On dispatch
            # failure, leave it un-deduped so the server's natural
            # redelivery retries us — otherwise the safety-net dedup
            # would itself cause data loss.
            handled = await _process(client, adapter, queue, msg)
            if handled:
                dedup.add(msg.id)
            _touch_heartbeat(heartbeat_file)

        # Periodic queue drain — same flow per queued message.
        now = time.monotonic()
        if now - last_drain >= drain_interval:
            last_drain = now
            await _drain(client, adapter, queue, dedup)
            _touch_heartbeat(heartbeat_file)

        # Empty poll → brief sleep so we don't spin on rare server quirks.
        if not messages:
            _touch_heartbeat(heartbeat_file)
            await asyncio.sleep(empty_poll_sleep)


async def _process(
    client: Any,
    adapter: ClawTellAdapter,
    queue: InboxQueue,
    msg: InboundMessage,
) -> bool:
    """Dispatch one inbound message. Returns True iff we've taken
    responsibility (acked or persisted-to-queue); False means the server
    should redeliver this id."""
    chat = adapter.get_chat(msg.from_name)
    if chat is None:
        queue.enqueue(
            QueuedMessage(
                id=msg.id,
                from_name=msg.from_name,
                subject=msg.subject,
                body=msg.body,
                received_at=msg.received_at,
                auto_reply_eligible=msg.auto_reply_eligible,
                queued_at=datetime.now(timezone.utc).isoformat(),
                raw=msg.raw,
            )
        )
        log.info(
            "no chat bound for tell/%s; queued msg %s", msg.from_name, msg.id
        )
        # Queued but not acked — the SERVER will redeliver. We dedup so
        # we don't re-enqueue the same id every cycle. The enqueue itself
        # is also id-idempotent as a belt-and-braces safeguard.
        return True

    try:
        if not msg.auto_reply_eligible:
            # Branch 2: forward inbound, do NOT invoke agent.
            await adapter.forward(HumanNotification(inbound=msg), chat)
            await asyncio.to_thread(client.ack, [msg.id])
            log.info(
                "forwarded ineligible msg %s from tell/%s to %s",
                msg.id,
                msg.from_name,
                chat.chat_id,
            )
            return True

        # Branch 3: eligible → invoke agent.
        reply = await adapter.inject(msg)
        await adapter.forward(HumanNotification(inbound=msg, reply=reply), chat)
        if reply and not reply.refusal:
            # Idempotency: if this id was already replied to in a prior
            # process incarnation that crashed before acking, skip the
            # send_back so the sender doesn't see a duplicate reply on
            # server redelivery.
            if queue.has_replied(msg.id):
                log.info(
                    "skipping duplicate send_back for msg %s (already replied "
                    "in prior run; this is the post-crash ack)",
                    msg.id,
                )
            else:
                try:
                    await asyncio.to_thread(
                        client.send,
                        msg.from_name,
                        reply.text,
                        f"Re: {msg.subject}" if msg.subject else None,
                    )
                    queue.mark_replied(msg.id)
                except Exception as e:
                    log.error(
                        "reply send-back failed for msg %s: %s", msg.id, e
                    )
        await asyncio.to_thread(client.ack, [msg.id])
        log.info("handled msg %s from tell/%s", msg.id, msg.from_name)
        return True

    except Exception as e:
        log.exception("dispatch failed for msg %s", msg.id)
        # Do NOT ack. The server's redelivery is our retry mechanism for
        # transient adapter failures. Return False so the caller doesn't
        # dedup this id — let the next poll cycle redeliver it.
        return False


async def _drain(
    client: Any,
    adapter: ClawTellAdapter,
    queue: InboxQueue,
    dedup: _DedupWindow,
) -> None:
    pending = queue.get_pending()
    if not pending:
        return
    log.info("draining %d queued messages", len(pending))
    for entry in pending:
        msg = InboundMessage(
            id=entry["id"],
            from_name=entry["from_name"],
            subject=entry.get("subject"),
            body=entry.get("body", ""),
            received_at=entry.get("received_at", ""),
            auto_reply_eligible=bool(entry.get("auto_reply_eligible", False)),
            raw=entry.get("raw", {}),
        )
        chat = adapter.get_chat(msg.from_name)
        if chat is None:
            continue  # still no chat bound for this sender
        try:
            if not msg.auto_reply_eligible:
                await adapter.forward(HumanNotification(inbound=msg), chat)
            else:
                reply = await adapter.inject(msg)
                await adapter.forward(
                    HumanNotification(inbound=msg, reply=reply), chat
                )
                if reply and not reply.refusal and not queue.has_replied(msg.id):
                    await asyncio.to_thread(
                        client.send,
                        msg.from_name,
                        reply.text,
                        f"Re: {msg.subject}" if msg.subject else None,
                    )
                    queue.mark_replied(msg.id)
            await asyncio.to_thread(client.ack, [msg.id])
            queue.dequeue(msg.id)
            dedup.add(msg.id)
        except Exception as e:
            log.warning("drain attempt failed for msg %s: %s", msg.id, e)
            queue.mark_attempt(msg.id, str(e))
