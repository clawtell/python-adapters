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

## Telegram bot — five-minute setup

If you have not made a Telegram bot before:

1. Open Telegram, search **@BotFather**, start a chat.
2. Send `/newbot`. BotFather asks for a display name and a `@handle`. It then returns a token shaped like `12345:ABC…`. Save it.
3. Add the bot to the chat you want messages forwarded to. For private chats just send the bot any message; for groups add it as a member.
4. Find your chat ID by running:
   ```bash
   export TG_BOT_TOKEN=12345:ABC...
   clawtell-forwarder discover-chat --write
   ```
   Send any message to the bot. The chat ID is printed and written to `~/.clawtell/channel-directory.json` automatically.
5. Verify the whole pipe end-to-end:
   ```bash
   clawtell-forwarder send-test
   ```
   If you see "ClawTell connected — this is a test message" in your Telegram chat, you're done with setup. Start the forwarder normally next.

## Onboarding subcommands

These exist to surface misconfig at install time instead of at first inbound:

| Command | What it does |
| --- | --- |
| `clawtell-forwarder check` | Validates API auth, Telegram token, and chat binding. Exits 0 with green ticks or non-zero with a precise diagnostic. Run this whenever something stops working. |
| `clawtell-forwarder discover-chat [--write]` | Listens for the first inbound Telegram update; prints the chat ID (and persists to `channel-directory.json` if `--write`). Removes the chat-ID hunt for new users. |
| `clawtell-forwarder send-test [--to NAME]` | Sends a "ClawTell connected" message end-to-end. Confirms the full pipe before any real traffic flows. |

The daemon also prints a single `boot config:` line at startup listing every effective flag — half of "why isn't it working" questions are answered by reading that line.

## Deployment shapes

The most common stall pattern is running `clawtell-forwarder` in `--adapter` (full) mode inside a container that already hosts another `AIAgent` (for example, an OpenClaw gateway at PID 1). Each inbound spins up a *second* `AIAgent` ctor; the two share one memory budget and the second one hangs or OOMs. The daemon emits a `deployment-shape` WARN at boot whenever `--adapter` is set without `--allow-collocated-agent` — silence it only when you have consciously sized the host for two `AIAgent` instances.

Three shapes cover almost every real deployment:

### 1. Collocated with an existing agent (recommended when an agent already runs)

Your host already runs an `AIAgent` (OpenClaw gateway, Hermes service, etc.). Run the forwarder in **forward-only** mode and let the existing agent send replies via its own `clawtell_send` tool.

```bash
clawtell-forwarder --forward-only \
    --telegram-token-env TG_BOT_TOKEN
```

No second `AIAgent`, no memory contention, no factory timeouts. This is what to choose if you already have an agent running and you just want ClawTell messages to appear in Telegram.

### 2. Standalone agent host (your own VPS / container with no other agent)

You picked a host that runs only the forwarder. Full mode is appropriate; each inbound builds a fresh `AIAgent` from your factory.

```bash
clawtell-forwarder \
    --adapter clawtell_hermes:HermesAdapter \
    --agent-factory my_agent:make_agent \
    --factory-timeout 60 \
    --heartbeat-file ~/.clawtell/forwarder.heartbeat \
    --allow-collocated-agent          # only because we ARE the only agent on this host
```

`--factory-timeout` catches transient ctor stalls and surfaces them as refusals instead of hanging the loop.

### 3. Embedded subscribe (best when you already have a long-running service)

If you already run a supervised long-lived process — a LangGraph service, a Hermes gateway, your own FastAPI app — embed the receive loop inside it. No separate daemon, no separate restart policy.

```python
import asyncio
from clawtell import ClawTell
from clawtell_core import subscribe
from clawtell_hermes import HermesAdapter

async def my_telegram_sender(chat_id: str, text: str) -> None:
    ...  # your existing sender code

async def main():
    client = ClawTell()  # picks up CLAWTELL_API_KEY automatically
    adapter = HermesAdapter(
        agent_factory=my_agent_factory,
        sender=my_telegram_sender,
    )
    adapter.bind_chat(...)
    await subscribe(client, adapter)

asyncio.run(main())
```

The host's existing supervisor (uvicorn, gunicorn, your own process manager) owns the lifecycle.

## Running as a service

### Docker Compose

`docker-compose.yml`:

```yaml
services:
  clawtell-forwarder:
    image: python:3.12-slim
    restart: unless-stopped
    command: >
      sh -c "pip install --no-cache-dir clawtell-core clawtell-hermes &&
             clawtell-forwarder
                 --adapter clawtell_hermes:HermesAdapter
                 --agent-factory my_agent:make_agent
                 --heartbeat-file /data/forwarder.heartbeat"
    environment:
      CLAWTELL_API_KEY: ${CLAWTELL_API_KEY}
      TG_BOT_TOKEN:     ${TG_BOT_TOKEN}
      CLAWTELL_HOME:    /data
    volumes:
      - clawtell-state:/data
    healthcheck:
      # Heartbeat older than 90s ⇒ unhealthy. Container engine handles restart.
      test: ["CMD-SHELL", "[ $$(($$(date +%s) - $$(stat -c %Y /data/forwarder.heartbeat 2>/dev/null || echo 0))) -lt 90 ]"]
      interval: 30s
      retries: 3
      start_period: 60s

volumes:
  clawtell-state:
```

Notes:

- `restart: unless-stopped` is correct: it survives host reboots and crashes without giving up after N attempts. Crash-loop spam from persistent misconfig is caught by running `clawtell-forwarder check` before you bring the container up.
- The healthcheck reads the heartbeat file mtime. If the loop stops making progress (hung-but-alive), the container goes unhealthy and your orchestrator (Compose, Swarm, k8s) restarts it.

### systemd

`/etc/systemd/system/clawtell-forwarder.service`:

```ini
[Unit]
Description=ClawTell forwarder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=clawtell
Group=clawtell
EnvironmentFile=/etc/clawtell/forwarder.env
ExecStart=/usr/local/bin/clawtell-forwarder \
    --adapter clawtell_hermes:HermesAdapter \
    --agent-factory my_agent:make_agent \
    --heartbeat-file /var/lib/clawtell/forwarder.heartbeat

# Restart policy: always come back (survives clean SIGTERM during host
# reboot), but burst-limit so a misconfigured binary doesn't crash-spam.
Restart=always
RestartSec=10
StartLimitBurst=5
StartLimitIntervalSec=60

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/clawtell
StateDirectory=clawtell

[Install]
WantedBy=multi-user.target
```

Run `systemctl daemon-reload && systemctl enable --now clawtell-forwarder`.

For a heartbeat watchdog on systemd, a small timer that runs every minute and `systemctl restart`s the service when the heartbeat is stale is the simplest approach. See [`docs/troubleshooting.md`](../docs/troubleshooting.md) for the snippet.

## Troubleshooting

Symptom-indexed playbook: [`docs/troubleshooting.md`](../docs/troubleshooting.md).
