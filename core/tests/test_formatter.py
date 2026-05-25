"""Lobster-banner output must include the brand banner, From line,
body, and (when present) Subject/Attachments — verify shape rather
than exact whitespace so we don't fight the template."""

from clawtell_core.adapter import AgentReply, InboundMessage
from clawtell_core.formatter import BANNER, format_inbound, format_reply


def _msg(**over) -> InboundMessage:
    base = dict(
        id="m1",
        from_name="alice",
        subject="quick q",
        body="ping?",
        received_at="2026-05-25T00:00:00Z",
        auto_reply_eligible=True,
        raw={},
    )
    base.update(over)
    return InboundMessage(**base)


def test_format_inbound_contains_banner_from_subject_body():
    out = format_inbound(_msg())
    assert BANNER in out
    assert "From: tell/alice" in out
    assert "Subject: quick q" in out
    assert "ping?" in out
    assert "Auto-reply eligible: True" in out
    assert "Attachments:" not in out


def test_format_inbound_omits_subject_line_when_none():
    out = format_inbound(_msg(subject=None))
    assert "Subject:" not in out
    assert "From: tell/alice" in out


def test_format_inbound_renders_attachments_from_raw():
    out = format_inbound(
        _msg(raw={"attachments": [{"filename": "a.txt"}, {"name": "b.png"}, "raw-id"]})
    )
    assert "Attachments: a.txt, b.png, raw-id" in out


def test_format_reply_contains_reply_block():
    out = format_reply(AgentReply(text="pong"), _msg())
    assert BANNER in out
    assert "→ Replied:" in out
    assert "pong" in out


def test_format_reply_with_refusal_marks_declined():
    out = format_reply(AgentReply(text="", refusal="not allowed"), _msg())
    assert "declined to reply: not allowed" in out
    assert "→ Replied:" not in out
