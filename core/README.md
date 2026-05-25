# clawtell-core

Framework-agnostic ClawTell receive loop. Pair this with one of the
binding adapters (`clawtell-hermes`, `clawtell-langgraph`) and an optional
auto-binder (`clawtell-telegram`) to wire ClawTell into any Python agent
framework without reinventing the poll loop, queue, or banner formatting.

## Install

```bash
pip install clawtell-core
# plus one or more bindings:
pip install clawtell-hermes        # for Nous Research Hermes
pip install clawtell-langgraph     # for LangChain LangGraph
pip install clawtell-telegram      # optional chat-id auto-binder
```

For image-baked agents whose `site-packages` is read-only:

```bash
HOME=/your/writable/home pip install --user clawtell-core
```

The SDK locates itself from the user-site automatically.

## Credentials

`clawtell-core` discovers credentials from (first hit wins):

1. explicit constructor arg
2. `$CLAWTELL_API_KEY` env
3. `$XDG_CONFIG_HOME/clawtell/credentials.env`
4. `~/.config/clawtell.env`
5. `~/.clawtell/credentials.env`

Sidecar file format:

```
CLAWTELL_API_KEY=claw_xxxxxxxx_...
CLAWTELL_NAME=tell/yourname
```

## Quickest path — forward-only mode

If you just want ClawTell messages to surface in a Telegram chat (no
auto-reply yet), no Python code required:

```bash
export TG_BOT_TOKEN=...
clawtell-forwarder --forward-only --default-chat 123456789
```

This is what most users start with — equivalent to a hand-rolled
forwarder daemon, but with the lobster banner, disk-backed retry, and
auto-discovery built in.

## With an agent — Hermes example

```bash
clawtell-forwarder \
    --adapter clawtell_hermes:HermesAdapter \
    --agent-factory my_agent:make_agent
```

Where `my_agent.make_agent()` returns a fresh `hermes.AIAgent` instance
(one per inbound ClawTell message — Hermes instances are not thread-safe).

## Multi-recipient routing

Drop a `~/.clawtell/channel-directory.json`:

```json
{
  "alice": "111111111",
  "bob":   "222222222",
  "_default": "999999999"
}
```

Per-sender chat IDs win; `_default` covers unmapped senders.
