"""SSE/poll transport coordinator for ``clawtell_core.subscribe``.

Drives the SDK's ``client.stream()`` (sync generator) inside a worker thread,
feeds messages into an ``asyncio.Queue``, and implements sticky poll fallback
after ``sse_failure_threshold`` consecutive SSE failures.

Mirrors the OpenClaw channel plugin's protocol behavior (timeout=120,
exponential backoff, Last-Event-ID resume header) but pushes the "what to do
when SSE fails" policy out of the SDK and into this transport layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

log = logging.getLogger(__name__)

DEFAULT_STREAM_TIMEOUT = 120
DEFAULT_STREAM_LIMIT = 50
DEFAULT_SSE_FAILURE_THRESHOLD = 3
DEFAULT_FALLBACK_TTL_SECONDS = 60.0
DEFAULT_BACKOFF_BASE_MS = 2000
DEFAULT_BACKOFF_CEILING_MS = 10_000


@dataclass
class _StreamError:
    exc: BaseException


_SENTINEL_END = object()


async def iter_messages(
    client: Any,
    *,
    stop_event: asyncio.Event,
    force_poll: bool = False,
    poll_timeout: int = 30,
    poll_limit: int = 50,
    stream_timeout: int = DEFAULT_STREAM_TIMEOUT,
    stream_limit: int = DEFAULT_STREAM_LIMIT,
    sse_failure_threshold: int = DEFAULT_SSE_FAILURE_THRESHOLD,
    sse_backoff_base_ms: int = DEFAULT_BACKOFF_BASE_MS,
    sse_backoff_ceiling_ms: int = DEFAULT_BACKOFF_CEILING_MS,
    fallback_ttl_seconds: float = DEFAULT_FALLBACK_TTL_SECONDS,
    empty_poll_sleep: float = 1.0,
) -> AsyncIterator[dict]:
    """Yield messages from ``client.stream()`` (SSE primary); on
    ``sse_failure_threshold`` consecutive failures, switch to ``client.poll()``
    for ``fallback_ttl_seconds``, then retry SSE.

    Honors ``force_poll=True`` and ``CLAWTELL_FORCE_POLL`` env to skip SSE
    entirely.
    """
    force = force_poll or bool(os.environ.get("CLAWTELL_FORCE_POLL"))
    consecutive_failures = 0
    fallback_until = 0.0
    last_event_id: Optional[str] = None

    while not stop_event.is_set():
        in_fallback = force or time.monotonic() < fallback_until

        if in_fallback:
            try:
                res = await asyncio.to_thread(
                    client.poll, timeout=poll_timeout, limit=poll_limit
                )
            except Exception as e:
                log.warning("[ClawTell] poll() error during fallback: %s", e)
                await asyncio.sleep(5.0)
                continue
            messages = res.get("messages") or []
            for msg in messages:
                last_event_id = msg.get("id") or last_event_id
                yield msg
            if not messages:
                await asyncio.sleep(empty_poll_sleep)
            if force:
                continue
            if time.monotonic() >= fallback_until:
                log.info(
                    "[ClawTell SSE] Fallback window expired, retrying SSE"
                )
                consecutive_failures = 0
                fallback_until = 0.0
            continue

        # SSE path
        try:
            stream_kwargs = dict(
                timeout=stream_timeout, limit=stream_limit, account=True
            )
            if last_event_id:
                stream_kwargs["last_event_id"] = last_event_id
            async for msg in _drive_stream(client, stream_kwargs, stop_event):
                last_event_id = msg.get("id") or last_event_id
                yield msg
            # stream returned cleanly (server timeout event, or end of stream)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            delay_ms = min(
                sse_backoff_base_ms * consecutive_failures,
                sse_backoff_ceiling_ms,
            )
            log.info(
                "[ClawTell SSE] connect/stream error #%d: %s; backoff %dms",
                consecutive_failures,
                e,
                delay_ms,
            )
            await asyncio.sleep(delay_ms / 1000.0)
            if consecutive_failures >= sse_failure_threshold:
                log.warning(
                    "[ClawTell SSE] %d consecutive failures, switching to "
                    "poll fallback for %ds",
                    consecutive_failures,
                    int(fallback_ttl_seconds),
                )
                fallback_until = time.monotonic() + fallback_ttl_seconds


async def _drive_stream(
    client: Any, kwargs: dict, stop_event: asyncio.Event
) -> AsyncIterator[dict]:
    """Run ``client.stream(**kwargs)`` (sync generator) in a worker thread,
    yield messages asynchronously. Raises on the producer thread's exception.
    """
    q: queue.Queue = queue.Queue(maxsize=128)
    loop = asyncio.get_running_loop()

    def producer():
        try:
            for msg in client.stream(**kwargs):
                if stop_event.is_set():
                    break
                q.put(msg)
            q.put(_SENTINEL_END)
        except BaseException as exc:
            q.put(_StreamError(exc))

    thread = threading.Thread(target=producer, daemon=True, name="clawtell-sse")
    thread.start()
    while True:
        item = await loop.run_in_executor(None, q.get)
        if item is _SENTINEL_END:
            return
        if isinstance(item, _StreamError):
            raise item.exc
        yield item
