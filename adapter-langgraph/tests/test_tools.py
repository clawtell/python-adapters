"""``make_clawtell_send_tool`` builds a LangChain tool whose run-time
output is one of the three verbatim status strings, matching the Hermes
plugin and OpenClaw plugin behavior."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from clawtell_langgraph.tools import make_clawtell_send_tool


class _StubClient:
    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[dict] = []

    def send(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"status": self.status}


def _invoke(tool, **kwargs):
    """Call the tool the way LangChain agents would — via .invoke()."""
    return tool.invoke(kwargs)


def test_tool_has_clawtell_send_name():
    t = make_clawtell_send_tool(_StubClient())
    assert t.name == "clawtell_send"


def test_immediate_send_returns_check_mark():
    c = _StubClient(status="delivered")
    t = make_clawtell_send_tool(c)
    out = _invoke(t, to="alice", body="hi")
    assert out == "✓ Sent to tell/alice"
    assert c.calls[0]["to"] == "alice"


def test_tell_prefix_is_stripped():
    c = _StubClient()
    t = make_clawtell_send_tool(c)
    _invoke(t, to="tell/bob", body="hi")
    assert c.calls[0]["to"] == "bob"


def test_pending_approval_status_returns_hourglass_string():
    c = _StubClient(status="pending_approval")
    t = make_clawtell_send_tool(c)
    out = _invoke(t, to="carol", body="hi")
    assert out == "… Pending approval — tell/carol will receive once approved"


def test_send_failure_returns_x_prefixed_string():
    class _BoomClient:
        def send(self, **_):
            raise RuntimeError("network down")

    t = make_clawtell_send_tool(_BoomClient())
    out = _invoke(t, to="dave", body="hi")
    assert out.startswith("× Send failed:")
    assert "network down" in out
