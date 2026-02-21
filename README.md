# connector-discord

Discord bridge for [kiso](https://github.com/kiso-run/core). The connector listens for messages in configured Discord channels (and DMs), forwards them to the kiso HTTP API for processing, and posts kiso's responses back to Discord. Responses are delivered via webhook callbacks from kiso, with an automatic polling fallback for reliability.

---

## Prerequisites

- kiso running (Docker recommended)
- A Discord bot application with the **Message Content Intent** enabled
- Python ≥ 3.11 and [`uv`](https://github.com/astral-sh/uv) (managed by kiso)

---

## Installation

```bash
kiso connector install discord
```

This clones the repo to `~/.kiso/connectors/discord/`, runs `uv sync`, and copies `config.example.toml` → `config.toml`.

---

## Configuration

### 1. Create a Discord bot

1. Go to <https://discord.com/developers/applications> and create a new application.
2. Navigate to **Bot** → enable **Message Content Intent** under *Privileged Gateway Intents*.
3. Copy the **Bot Token**.
4. Generate an invite URL (**OAuth2 → URL Generator**) with scopes `bot` and permissions: *Send Messages*, *Read Message History*, *View Channels*.
5. Invite the bot to your server.

### 2. Set environment variables

```bash
kiso env set KISO_CONNECTOR_DISCORD_BOT_TOKEN "your-discord-bot-token"
kiso env set KISO_CONNECTOR_DISCORD_KISO_TOKEN "your-kiso-api-token"
kiso env reload
```

### 3. Configure the kiso API token

Add the token to `~/.kiso/config.toml`. The key name (`discord`) determines which `aliases.*` field is used for user resolution:

```toml
[tokens]
cli = "existing-cli-token"
discord = "same-value-as-KISO_CONNECTOR_DISCORD_KISO_TOKEN"
```

### 4. Map Discord users

For each Discord user that should be recognised, add an alias in `~/.kiso/config.toml`:

```toml
[users.alice]
role = "admin"
aliases.discord = "alice_discord_username"

[users.bob]
role = "user"
aliases.discord = "bob#1234"
```

The alias value must match `message.author.name` as seen by discord.py (typically the username without discriminator on newer accounts).

### 5. Map Discord channels

Edit `~/.kiso/connectors/discord/config.toml` and add channel IDs:

```toml
kiso_api = "http://localhost:8333"
webhook_port = 9001
webhook_host = "0.0.0.0"
bot_prefix = ""

[channels]
"1234567890123456789" = "discord-general"
"9876543210987654321" = "discord-dev"
```

To find a channel ID: enable **Developer Mode** in Discord settings, then right-click the channel → *Copy Channel ID*.

---

## Running

```bash
kiso connector run discord
```

Check status:

```bash
kiso connector status discord
```

View logs:

```bash
tail -f ~/.kiso/connectors/discord/connector.log
```

Stop:

```bash
kiso connector stop discord
```

---

## How it works

```
User types in Discord channel
        │
        ▼
connector: POST /msg → kiso API
        │
        ▼ (kiso processes the request)
        │
  ┌─────┴──────────────────┐
  │ webhook (primary)       │  polling fallback (after 30 s)
  │ POST /callback          │  GET /status/{session}?after={id}
  └─────────────────────────┘
        │
        ▼
connector: channel.send(response)
        │
        ▼
Discord channel / DM
```

- **Webhook**: kiso POSTs to `http://connector-host:9001/callback` after each `msg` task.
- **Polling fallback**: if no webhook arrives within 30 s, the connector polls `GET /status/{session}` every 5 s until `final=true` or the worker goes idle.

---

## User mapping

The connector uses the Discord username (`message.author.name`) as the `user` field in `POST /msg`. Kiso resolves this against `users.*.aliases.discord` in its config. Users without a matching alias receive a `202 untrusted` response — their messages are saved for audit but not processed.

---

## Optional: prefix filter

Set `bot_prefix` in `config.toml` to restrict which messages the bot responds to:

```toml
bot_prefix = "!kiso "
```

With this set, only messages starting with `!kiso ` are forwarded (the prefix is stripped before forwarding).

---

## Docker

Expose the webhook port in your `docker-compose.yml`:

```yaml
services:
  kiso:
    ports:
      - "9001:9001"
```

Make sure `webhook_host = "0.0.0.0"` in `config.toml` so the server binds to all interfaces.

---

## Troubleshooting

**Bot is not responding to messages**
- Check that the channel ID is in `[channels]` in `config.toml`.
- Verify that the Discord username matches `aliases.discord` in `~/.kiso/config.toml`.
- Check logs: `~/.kiso/connectors/discord/connector.log`.

**Webhook failures / responses not arriving**
- Ensure port 9001 is exposed and reachable from the kiso container.
- Look for `Webhook server listening` in the logs on startup.

**Messages accepted but not processed**
- The user is untrusted: check that `aliases.discord` in kiso config matches the exact Discord username.
- The kiso token name (`discord` in `[tokens]`) must match the alias key (`aliases.discord`).

**Bot token / kiso token errors**
- Run `kiso env list` to verify both `KISO_CONNECTOR_DISCORD_BOT_TOKEN` and `KISO_CONNECTOR_DISCORD_KISO_TOKEN` are set.

---

## License

MIT
