"""Disk-backed inbox queue for messages received while no chat is bound or
adapter dispatch is failing. Ported from
``Repos/channel/src/queue.ts`` — same shape (`{pending, deadLetter}`), same
retry limits, same `0o600` permission. Lives at
``~/.clawtell/inbox-queue.json`` (NOT under ``~/.openclaw/`` — the
clawtell-core package is framework-agnostic by design).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
DEAD_LETTER_CAP = 100


def _queue_path() -> Path:
    base = Path(os.environ.get("CLAWTELL_HOME") or (Path.home() / ".clawtell"))
    return base / "inbox-queue.json"


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
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"pending": [], "deadLetter": []}
        except json.JSONDecodeError as e:
            log.warning("queue file corrupt (%s); starting fresh", e)
            return {"pending": [], "deadLetter": []}

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

    def get_pending(self) -> list[dict]:
        with self._lock:
            return list(self._read()["pending"])

    def get_dead_letter(self) -> list[dict]:
        with self._lock:
            return list(self._read()["deadLetter"])
