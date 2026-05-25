"""Verify the inbound message shape — particularly the ``tell/`` strip
and defensive ``auto_reply_eligible`` read that the rest of the loop
relies on."""

from clawtell_core.adapter import (
    AgentReply,
    ChatTarget,
    ClawTellAdapter,
    HumanNotification,
    InboundMessage,
)


def test_inbound_message_from_dict_strips_tell_prefix():
    msg = InboundMessage.from_dict(
        {
            "id": "m_1",
            "from": "tell/alice",
            "subject": "hi",
            "body": "hello",
            "created_at": "2026-05-25T00:00:00Z",
            "auto_reply_eligible": True,
        }
    )
    assert msg.from_name == "alice"
    assert msg.id == "m_1"
    assert msg.body == "hello"
    assert msg.auto_reply_eligible is True


def test_inbound_message_handles_missing_auto_reply_field():
    msg = InboundMessage.from_dict({"id": "m_2", "from": "bob"})
    assert msg.auto_reply_eligible is False


def test_inbound_message_alternate_keys():
    msg = InboundMessage.from_dict(
        {
            "messageId": "m_3",
            "from_name": "carol",
            "body_decrypted": "decrypted text",
            "createdAt": "2026-05-25T00:00:00Z",
        }
    )
    assert msg.id == "m_3"
    assert msg.from_name == "carol"
    assert msg.body == "decrypted text"


def test_inbound_keeps_raw_for_extension_fields():
    raw = {"id": "m_4", "from": "tell/dave", "custom_field": "xyz"}
    msg = InboundMessage.from_dict(raw)
    assert msg.raw["custom_field"] == "xyz"


class _StubAdapter(ClawTellAdapter):
    async def inject(self, msg):
        return None

    async def forward(self, notification, target):
        return True


def test_adapter_bind_chat_and_directory_resolution():
    a = _StubAdapter()
    a.bind_directory({"alice": "111", "_default": "999"})
    assert a.get_chat("alice").chat_id == "111"
    assert a.get_chat("zoe").chat_id == "999"
    # directory takes precedence over single-chat bind
    a.bind_chat(ChatTarget(channel="telegram", chat_id="888"))
    assert a.get_chat("alice").chat_id == "111"


def test_adapter_falls_back_to_single_chat_with_no_directory():
    a = _StubAdapter()
    a.bind_chat(ChatTarget(channel="telegram", chat_id="555"))
    assert a.get_chat().chat_id == "555"
    assert a.get_chat("anyone").chat_id == "555"


def test_human_notification_carries_reply_optionally():
    inbound = InboundMessage(
        id="m_5",
        from_name="ed",
        subject=None,
        body="hi",
        received_at="",
        auto_reply_eligible=False,
    )
    n = HumanNotification(inbound=inbound)
    assert n.reply is None
    n2 = HumanNotification(inbound=inbound, reply=AgentReply(text="hello back"))
    assert n2.reply.text == "hello back"
