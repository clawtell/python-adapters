# Integration tests

These tests exercise the four packages end-to-end against the live
ClawTell staging environment. They are NOT run by the default
`pytest` invocation (the per-package `tests/` suites cover unit-level
contracts without network access).

## Required environment

```bash
export CLAWTELL_API_KEY=...        # test account A — the recipient
export CLAWTELL_API_KEY_PEER=...   # test account B — the sender
export TG_BOT_TOKEN=...            # test Telegram bot
export TG_TEST_CHAT_ID=...         # chat the bot can post to
```

## Run

```bash
pytest integration_tests/ -m integration
```

## Suite (per the implementation plan)

1. **Hermes happy path** — minimal `AIAgent` factory + mock Telegram
   sender; peer sends "what is 2+2?"; assert sender called with
   chat_id=$TG_TEST_CHAT_ID, text contains "4", clawtell `send`
   echoed back to peer.
2. **LangGraph happy path** — minimal react agent + SQLite checkpointer;
   same assertions; second message in same thread → thread state
   persisted across messages.
3. **LangGraph interrupt resume** — graph that calls `interrupt()`;
   first message hits interrupt; second message resumes with
   `Command(resume=...)`; assert final state advances.
4. **No-chat-bound queue** — start adapter without `bind_chat`; send
   message; assert `~/.clawtell/inbox-queue.json` has the message in
   `pending`; `client.ack` NOT called; bind chat; within 60s queue
   drains and ack lands.
5. **Auto-binder** — `TelegramBridge` attached; simulate inbound
   Telegram update (via mock transport); assert chat bound; restart
   bridge with no new update; assert persisted chat is restored.
6. **Auto-reply gate** — peer is not on recipient's allowlist;
   `auto_reply_eligible=false`; assert `adapter.inject` NOT called,
   inbound forwarded to human chat, message acked.
7. **Concurrency (Hermes)** — fire 5 messages to same recipient within
   1s; assert 5 separate `AIAgent` instances; all 5 process.
8. **Concurrency (LangGraph)** — fire 5 messages to same thread within
   1s; assert messages process serially; checkpoint contains all 5 in
   order.

## Reference agents

`langgraph_reference/` — minimal LangGraph react agent for tests 2-3
and the v1 manual gate.
