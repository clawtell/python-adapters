# clawtell-telegram

Telegram auto-binder for ClawTell adapters. Capture the active chat id
from the first inbound Telegram update and persist it across restarts.

## Install

```bash
pip install clawtell-telegram
```

## Single-active-chat setup

```python
import asyncio
import os
from clawtell import ClawTell
from clawtell_core import subscribe
from clawtell_hermes import HermesAdapter
from clawtell_telegram import TelegramBridge

async def main():
    client = ClawTell()
    adapter = HermesAdapter(agent_factory=..., sender=...)  # see clawtell-hermes
    bridge = TelegramBridge(bot_token=os.environ["TG_BOT_TOKEN"])
    bridge.attach(adapter)                       # restores ~/.clawtell/chat.json
    asyncio.create_task(bridge.run())            # captures chat on first update
    await subscribe(client, adapter)

asyncio.run(main())
```

The first time someone messages your bot, the bridge writes the chat id
to `~/.clawtell/chat.json` and binds it to the adapter. Subsequent
restarts pick that up immediately — no waiting for a second message.

## Multi-recipient setup

For multiple senders → different chats, write a directory file at
`~/.clawtell/channel-directory.json`:

```json
{
  "alice": "111111111",
  "bob":   "222222222",
  "_default": "333333333"
}
```

`clawtell-forwarder` loads this on startup; you don't need
`TelegramBridge` in that mode.

## State

- `~/.clawtell/chat.json` — persisted active chat (mode `0o600`).
- `CLAWTELL_HOME` overrides the base directory.
