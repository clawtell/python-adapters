# clawtell-hermes

ClawTell binding adapter for the [Nous Research Hermes agent
framework](https://github.com/NousResearch/hermes-agent). Pair with
`clawtell-core` for the receive loop.

## Install

```bash
pip install clawtell-hermes
```

For image-baked agents whose `site-packages` is read-only:

```bash
HOME=/your/writable/home pip install --user clawtell-hermes
```

Add `hermes-agent` and `clawtell-core` from the same install (they pull
in transitively).

## Quickest path

```bash
clawtell-forwarder \
    --adapter clawtell_hermes:HermesAdapter \
    --agent-factory mypkg.my_hermes:make_agent
```

Where `mypkg/my_hermes.py` exposes:

```python
def make_agent():
    # Hermes instances are not thread-safe — a fresh one per message is
    # the recommended default. Override agent_pool_size in HermesAdapter
    # if you want a sticky session per sender (memory cost: 1 AIAgent each).
    from hermes import AIAgent
    return AIAgent(...)
```

## With outbound tool (let the agent initiate ClawTell sends)

In your Hermes plugin entrypoint:

```python
from clawtell_hermes.plugin import register as register_clawtell

def register(ctx):
    register_clawtell(ctx)   # adds clawtell_send to the agent's toolset
```

The agent can now call `clawtell_send({to: "alice", body: "…"})`.

## What the adapter does for you

- Per-message `AIAgent` instantiation (or LRU pool, off by default).
- Lobster-banner formatting for the Telegram chat (matches OpenClaw).
- Disk-backed retry queue when no chat is bound or sends fail.
- 3-branch dispatch: queue / forward-only-when-not-eligible / agent reply.
- Credential discovery from `~/.config/clawtell.env`, `/opt/data/.env`,
  env vars, and explicit args.
- Telegram bot token discovery via `/proc/1/environ` for image-baked
  agents whose worker process didn't inherit it.
