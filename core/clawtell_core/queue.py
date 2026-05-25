"""Disk-backed inbox queue for messages received while no chat is bound or
adapter dispatch is failing. Ported from
``Repos/channel/src/queue.ts`` — same shape (``{pending, deadLetter}``),
same retry limits, same ``0o600`` permission. Lives at
``~/.clawtell/inbox-queue.json`` (NOT under ``~/.openclaw/`` — the
clawtell-core package is framework-agnostic by design).

File schema is versioned (``version: 1``) so future changes can migrate
old installs in place. Corrupt files are backed up to
``inbox-queue.corrupt.<unix-ts>.json`` before the queue resets, instead
of being silently truncated — silent data loss is unacceptable.

A rolling ``replied`` list tracks recently-completed ``client.send()``
calls so that a crash between send-reply and ack does NOT cause a
double reply on server redelivery.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
DEAD_LETTER_CAP = 100
REPLIED_CAP = 1000
SCHEMA_VERSION = 1


def _queue_path() -> Path:
    base = Path(os.environ.get("CLAWTELL_HOME") or (Path.home() / ".clawtell"))
    return base / "inbox-queue.json"


def _empty_state() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "pending": [],
        "deadLetter": [],
        "replied": [],
    }


@dataclass
class QueuedMessage:
    id: str
    from_name: str
    subject: Optional[str]
    body: str
    received_at: str
    auto_reply_eligible: bool
    queued_at: str
    attempts: int = 0
    last_error: str = ""
    raw: dict = field(default_factory=dict)


class InboxQueue:
    """Process-local lock + on-disk persistence. Multi-process access is
    not supported — one subscribe() loop per process."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _queue_path()
        self._lock = Lock()

    def _read(self) -> dict:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty_state()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            backup = self._path.with_name(
                f"inbox-queue.corrupt.{int(time.time())}.json"
            )
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(raw, encoding="utf-8")
                os.chmod(backup, 0o600)
                log.error(
                    "queue file corrupt (%s); backed up to %s and starting fresh",
                    e,
                    backup,
                )
            except OSError as backup_err:
                log.error(
                    "queue file corrupt (%s); backup ALSO failed (%s); "
                    "starting fresh anyway",
                    e,
                    backup_err,
                )
            return _empty_state()
        # Forward-compat: ensure new keys are present even if reading a
        # pre-versioned file (older clawtell-core wrote no ``version``,
        # ``replied`` keys).
        data.setdefault("version", 0)
        data.setdefault("pending", [])
        data.setdefault("deadLetter", [])
        data.setdefault("replied", [])
        data["version"] = SCHEMA_VERSION
        return data

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".inbox-queue.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def enqueue(self, msg: QueuedMessage) -> None:
        with self._lock:
            data = self._read()
            if any(m["id"] == msg.id for m in data["pending"]):
                return
            data["pending"].append(asdict(msg))
            self._write(data)
        log.info("queued msg %s from tell/%s", msg.id, msg.from_name)

    def dequeue(self, msg_id: str) -> None:
        with self._lock:
            data = self._read()
            data["pending"] = [m for m in data["pending"] if m["id"] != msg_id]
            self._write(data)

    def mark_attempt(self, msg_id: str, error: str) -> Optional[dict]:
        """Increment attempt counter; dead-letter at MAX_ATTEMPTS.
        Returns the dead-lettered message dict, or None if still pending."""
        with self._lock:
            data = self._read()
            found = next((m for m in data["pending"] if m["id"] == msg_id), None)
            if not found:
                return None
            found["attempts"] = int(found.get("attempts", 0)) + 1
            found["last_error"] = error
            if found["attempts"] >= MAX_ATTEMPTS:
                data["pending"] = [m for m in data["pending"] if m["id"] != msg_id]
                data["deadLetter"].append(found)
                if len(data["deadLetter"]) > DEAD_LETTER_CAP:
                    data["deadLetter"] = data["deadLetter"][-DEAD_LETTER_CAP:]
                self._write(data)
                log.warning(
                    "dead-letter msg %s after %d attempts: %s",
                    msg_id,
                    found["attempts"],
                    error,
                )
                return found
            self._write(data)
            return None

    def mark_replied(self, msg_id: str) -> None:
        """Record that we successfully called ``client.send()`` for this
        inbound's reply. Used to prevent double-reply if we crash before
        the subsequent ack reaches the server and the message is
        redelivered."""
        with self._lock:
            data = self._read()
            replied = data.get("replied", [])
            if any(entry.get("id") == msg_id for entry in replied):
                return
            replied.append({"id": msg_id, "ts": int(time.time())})
            if len(replied) > REPLIED_CAP:
                replied = replied[-REPLIED_CAP:]
            data["replied"] = replied
            self._write(data)

    def has_replied(self, msg_id: str) -> bool:
        with self._lock:
            data = self._read()
            return any(
                entry.get("id") == msg_id for entry in data.get("replied", [])
            )

    def get_pending(self) -> list[dict]:
        with self._lock:
            return list(self._read()["pending"])

    def get_dead_letter(self) -> list[dict]:
        with self._lock:
            return list(self._read()["deadLetter"])
