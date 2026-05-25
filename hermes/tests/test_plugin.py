"""Plugin registration: ``register(ctx, client=...)`` calls
``ctx.register_tool`` once with the expected schema and handler. The
handler maps client.send results to the three verbatim status strings."""

from __future__ import annotations

import pytest

from clawtell_hermes.plugin import register


class _StubCtx:
    def __init__(self) -> None:
        self.registered: dict = {}

    def register_tool(self, **kwargs) -> None:
        self.registered = kwargs


class _StubClient:
    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[dict] = []

    def send(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"status": self.status}


def test_register_adds_clawtell_send_tool():
    ctx = _StubCtx()
    client = _StubClient()
    register(ctx, client=client)
    assert ctx.registered["name"] == "clawtell_send"
    assert "to" in ctx.registered["schema"]["properties"]
    assert ctx.registered["schema"]["required"] == ["to", "body"]


def test_handler_returns_immediate_success_string():
    ctx = _StubCtx()
    client = _StubClient(status="delivered")
    register(ctx, client=client)
    h = ctx.registered["handler"]
    out = h({"to": "alice", "body": "hi"})
    assert out == "✓ Sent to tell/alice"
    assert client.calls[0]["to"] == "alice"


def test_handler_strips_tell_prefix():
    ctx = _StubCtx()
    client = _StubClient()
    register(ctx, client=client)
    h = ctx.registered["handler"]
    h({"to": "tell/bob", "body": "hi"})
    assert client.calls[0]["to"] == "bob"


def test_handler_returns_pending_approval_string():
    ctx = _StubCtx()
    client = _StubClient(status="pending_approval")
    register(ctx, client=client)
    h = ctx.registered["handler"]
    out = h({"to": "carol", "body": "hi"})
    assert out == "… Pending approval — tell/carol will receive once approved"


def test_handler_returns_failure_string_on_exception():
    ctx = _StubCtx()

    class _BoomClient:
        def send(self, **_):
            raise RuntimeError("dns down")

    register(ctx, client=_BoomClient())
    h = ctx.registered["handler"]
    out = h({"to": "dave", "body": "hi"})
    assert out.startswith("× Send failed:")
    assert "dns down" in out
