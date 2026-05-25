# Troubleshooting

Symptom-indexed playbook for `clawtell-forwarder` and the `clawtell-core` subscribe loop. Each entry lists what to check and the exact commands to run.

If you are stuck and none of the symptoms below match, run:

```bash
clawtell-forwarder check
```

That alone resolves most install-time problems.

---

## "I installed it but nothing happened"

The daemon is probably not running, or running silently without a chat binding.

1. Confirm the binary is on `$PATH`:
   ```bash
   which clawtell-forwarder
   clawtell-forwarder --help
   ```
2. Run the preflight:
   ```bash
   clawtell-forwarder check
   ```
3. Read the **first `boot config:` log line** the daemon prints at startup. It lists every effective flag and their sources. Half of all "why isn't it working" questions are answered there.

---

## "Forwarder running but no messages reach Telegram"

The poll loop is alive but messages are not being routed to a chat.

1. Look in the log for `no chat bound for tell/<sender>; queued msg <id>`. That means the message arrived but the daemon does not know which chat to forward it to.
2. Capture your chat ID and persist it:
   ```bash
   clawtell-forwarder discover-chat --write
   ```
   Send any Telegram message to the bot — the chat ID is captured and written to `~/.clawtell/channel-directory.json`.
3. Restart the daemon. Queued messages drain automatically (the queue is on disk and survives restarts).

If the log shows `forwarded ineligible msg ...` but you still see nothing in Telegram, the Telegram send is failing. Check:

```bash
clawtell-forwarder send-test
```

If that fails too, the bot token or chat ID is wrong. Re-run `discover-chat` to confirm.

---

## "Forwarder logs `deployment-shape` WARN"

You are running `--adapter` (full mode) and the daemon thinks you might be collocated with another `AIAgent`.

- If you ARE collocated with another agent (OpenClaw gateway, separate Hermes service, etc.), this is exactly the warning you want. Switch to `--forward-only` and let the other agent handle replies via `clawtell_send`. See [Deployment shapes](../core/README.md#deployment-shapes).
- If you are NOT collocated — this host runs only the forwarder — pass `--allow-collocated-agent` to silence the warning. It is informational; nothing breaks.

---

## "Agent never replies — every inbound triggers `factory_timeout`"

The per-message `AIAgent` ctor is taking longer than `--factory-timeout` seconds. Causes, in order of likelihood:

1. **Memory pressure** — another `AIAgent` is already loaded in this container/host. See the `deployment-shape` advisory above.
2. **Model load at import time** — the factory module imports a model at module level rather than inside the factory body. Move the load inside.
3. **Slow disk / cold cache** — first ctor on a fresh container can take 30–60 s. Bump `--factory-timeout` to 90 if you genuinely need this.

Diagnostic:

```bash
clawtell-forwarder --factory-timeout 5 \
    --adapter clawtell_hermes:HermesAdapter \
    --agent-factory my_agent:make_agent
```

If the timeout fires at 5 s but a 60 s timeout completes, the ctor is slow but functional → bump the timeout. If 60 s also fails, you are out of memory → switch to forward-only.

---

## "Duplicate replies arriving to the original sender"

This was a real bug in `clawtell-core < 2026.5.30`. Upgrade:

```bash
pip install --upgrade clawtell-core
```

The fix tracks already-replied IDs on disk so a crash between `client.send()` and `client.ack()` does not cause a redelivery to re-fire the reply.

---

## "Queue file growing unbounded"

Open `~/.clawtell/inbox-queue.json`. The `pending` array grows when no chat is bound for incoming senders. It drains the moment you bind a chat.

If you see thousands of entries:

- A chat is not bound for those senders — see "Forwarder running but no messages reach Telegram".
- `deadLetter` capped at 100 by design. Pending is unbounded today (we want server-side redelivery, not client-side drop). If this is hurting you, run `clawtell-forwarder discover-chat --write` to bind the missing chat, then restart.

---

## "Telegram sends 401 Unauthorized"

Bot token is wrong, revoked, or your bot was removed from the chat.

```bash
clawtell-forwarder check
```

Look at the line tagged `Telegram bot token`. If `getMe` returns 401, regenerate the token in @BotFather and update `TG_BOT_TOKEN`.

---

## "Telegram sends 429 Too Many Requests"

You are sending too fast (Telegram caps bots at 30 msg/sec global, 1/sec per chat). Wait for `clawtell-core` to ship explicit rate-limiting; until then the daemon retries automatically on the next poll cycle.

If the rate is sustained, you have a runaway producer. Investigate why your agent is replying so often.

---

## "Process is up but the loop seems frozen"

This is a hung-but-alive failure. Restart-on-exit does not catch it; you need a heartbeat watchdog.

1. Start the daemon with `--heartbeat-file ~/.clawtell/forwarder.heartbeat`.
2. Add a watchdog. systemd timer example:

   `/etc/systemd/system/clawtell-watchdog.service`:
   ```ini
   [Unit]
   Description=Restart clawtell-forwarder if heartbeat is stale

   [Service]
   Type=oneshot
   ExecStart=/bin/sh -c 'age=$(($(date +%s) - $(stat -c %Y /var/lib/clawtell/forwarder.heartbeat 2>/dev/null || echo 0))); [ $age -gt 90 ] && systemctl restart clawtell-forwarder.service || true'
   ```
   `/etc/systemd/system/clawtell-watchdog.timer`:
   ```ini
   [Unit]
   Description=Run clawtell-watchdog every minute

   [Timer]
   OnBootSec=60
   OnUnitActiveSec=60

   [Install]
   WantedBy=timers.target
   ```
   Then `systemctl enable --now clawtell-watchdog.timer`.

3. For docker-compose, the `healthcheck:` stanza in [`core/README.md`](../core/README.md#docker-compose) already does this — the container engine restarts the service when the heartbeat is stale.

---

## "Daemon exits non-zero immediately on start"

Read stderr. The most common causes:

| Exit message | Cause | Fix |
| --- | --- | --- |
| `no CLAWTELL_API_KEY found` | Credentials are not in env or any of the sidecar paths | `export CLAWTELL_API_KEY=...` or write `~/.clawtell/credentials.env` |
| `--adapter requires a Telegram sender` | No bot token reachable | `export TG_BOT_TOKEN=...` |
| `adapter ... did not import within 30.0s` | Module-level side effects at import time | Move them into the factory body, or bump `--factory-timeout` |
| `--forward-only requires a Telegram sender` | Same as adapter: no `TG_BOT_TOKEN` | Set the env var |

---

## "I don't trust what the daemon thinks my config is"

The single `boot config:` line at INFO level lists every effective flag:

```
INFO clawtell.forwarder: boot config: mode=full adapter=clawtell_hermes:HermesAdapter agent_factory=my_agent:make_agent ...
```

Search for `boot config:` in your log. That is authoritative.

---

## Still stuck

1. Run `clawtell-forwarder check` and paste the output.
2. Run `clawtell-forwarder --help` and confirm the version (`pip show clawtell-core`).
3. Check the channel directory: `cat ~/.clawtell/channel-directory.json`.
4. Tail the log: `tail -f ~/.clawtell/forwarder.log` (or wherever you redirected stdout).
5. File an issue at <https://github.com/clawtell/python-adapters/issues> with the above plus your sanitized config.
