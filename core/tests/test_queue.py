"""Disk queue port from queue.ts — exercises persistence, attempt
counting, dead-letter cap, and id deduplication."""

from clawtell_core.queue import DEAD_LETTER_CAP, MAX_ATTEMPTS, InboxQueue, QueuedMessage


def _msg(i: str, **over) -> QueuedMessage:
    base = dict(
        id=i,
        from_name="alice",
        subject="hi",
        body="hello",
        received_at="2026-05-25T00:00:00Z",
        auto_reply_eligible=False,
        queued_at="2026-05-25T00:00:01Z",
    )
    base.update(over)
    return QueuedMessage(**base)


def test_enqueue_then_get_pending():
    q = InboxQueue()
    q.enqueue(_msg("a"))
    q.enqueue(_msg("b"))
    pending = q.get_pending()
    assert {m["id"] for m in pending} == {"a", "b"}


def test_enqueue_is_idempotent_on_id():
    q = InboxQueue()
    q.enqueue(_msg("a"))
    q.enqueue(_msg("a", body="DIFFERENT"))
    pending = q.get_pending()
    assert len(pending) == 1
    # first-write wins
    assert pending[0]["body"] == "hello"


def test_dequeue_removes_entry():
    q = InboxQueue()
    q.enqueue(_msg("a"))
    q.enqueue(_msg("b"))
    q.dequeue("a")
    assert {m["id"] for m in q.get_pending()} == {"b"}


def test_mark_attempt_increments_and_dead_letters():
    q = InboxQueue()
    q.enqueue(_msg("a"))
    for i in range(MAX_ATTEMPTS - 1):
        result = q.mark_attempt("a", f"error #{i}")
        assert result is None
    final = q.mark_attempt("a", "final error")
    assert final is not None
    assert final["attempts"] == MAX_ATTEMPTS
    assert q.get_pending() == []
    assert q.get_dead_letter()[0]["id"] == "a"


def test_dead_letter_cap():
    q = InboxQueue()
    # Fan out way over the cap
    over = DEAD_LETTER_CAP + 5
    for i in range(over):
        q.enqueue(_msg(f"m{i}"))
        for _ in range(MAX_ATTEMPTS - 1):
            q.mark_attempt(f"m{i}", "x")
        q.mark_attempt(f"m{i}", "final")
    dl = q.get_dead_letter()
    assert len(dl) == DEAD_LETTER_CAP
    # the most recent failures are retained
    assert dl[-1]["id"] == f"m{over - 1}"


def test_persistence_survives_new_instance(isolated_clawtell_home):
    q1 = InboxQueue()
    q1.enqueue(_msg("persist"))
    q2 = InboxQueue()
    assert {m["id"] for m in q2.get_pending()} == {"persist"}


def test_mark_attempt_on_unknown_id_returns_none():
    q = InboxQueue()
    assert q.mark_attempt("ghost", "x") is None
