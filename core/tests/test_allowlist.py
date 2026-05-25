"""Defensive local allowlist — fail-closed, case-insensitive, ``tell/``
prefix is stripped on both sides for comparison."""

from clawtell_core.allowlist import is_auto_reply_allowed


def test_manual_only_always_denies():
    assert not is_auto_reply_allowed("alice", mode="manual_only")
    assert not is_auto_reply_allowed("alice", mode="manual_only", allowlist=["alice"])


def test_everyone_always_allows():
    assert is_auto_reply_allowed("alice", mode="everyone")
    assert is_auto_reply_allowed("anyone", mode="everyone", allowlist=[])


def test_allowlist_match_case_insensitive_and_prefix_stripped():
    assert is_auto_reply_allowed("alice", allowlist=["Alice"])
    assert is_auto_reply_allowed("tell/Alice", allowlist=["alice"])
    assert is_auto_reply_allowed("alice", allowlist=["tell/ALICE"])


def test_allowlist_miss_denies():
    assert not is_auto_reply_allowed("bob", allowlist=["alice"])


def test_no_allowlist_no_mode_fails_closed():
    assert not is_auto_reply_allowed("alice")


def test_implicit_mode_when_allowlist_provided():
    """No mode + non-empty allowlist → behaves as allowlist_only."""
    assert is_auto_reply_allowed("alice", allowlist=["alice"])
    assert not is_auto_reply_allowed("bob", allowlist=["alice"])
