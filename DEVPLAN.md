# connector-discord — Development Plan

## Overview

Discord bridge for kiso. Receives Discord messages and forwards them to the kiso HTTP API, then delivers kiso responses back to Discord channels via webhook callbacks with a polling fallback.

## Architecture

Single-file connector (`run.py`, 586 LOC) with three subsystems:

1. **Discord bot** — `discord.py` client that listens for messages across configured channels and DMs, applies prefix filtering, and forwards to kiso via `httpx.AsyncClient`.
2. **Webhook server** — `aiohttp` HTTP server on a configurable port that receives kiso response callbacks with optional HMAC-SHA256 signature verification.
3. **Polling fallback** — if no webhook arrives within 30 seconds, polls `GET /status/{session}` every 5 seconds until a final response is received.

**Data flow:**
```
Discord msg → _handle_message → forward_message (POST /msg)
                                       ↓
                              kiso processes request
                                       ↓
                   webhook callback (POST /callback) ──→ deliver_to_discord
                              or (30s timeout)
                   polling fallback (GET /status)   ──→ deliver_to_discord
```

**Key types:**
- `DiscordConnector` — main class, holds runtime state (channel map, pending sessions, httpx client)
- `PendingSession` — tracks in-flight requests (last_task_id, webhook_received event, final_received flag)
- `WebhookPayload` — parsed webhook callback data

**Dependencies:** `discord.py`, `httpx` (async), `aiohttp`, `tomllib` (stdlib)

## Capabilities

| Capability | Status | Notes |
|---|---|---|
| Message forwarding (channel → kiso) | Done | Prefix filtering, empty content guard |
| DM support | Done | Dynamic session `discord-dm-{user_id}` |
| Webhook response delivery | Done | HMAC-SHA256 signature verification |
| Polling fallback | Done | 30s wait → 5s interval polling |
| Channel map normalization | Done | TOML int keys → str |
| Message splitting | Done | Paragraph-aware, hard split fallback |
| Graceful shutdown | Done | SIGTERM/SIGINT handlers, cleanup chain |
| Unit test suite | Done | 51 passing, 8 skipped (integration) |
| Connector method tests | Pending | M6 |
| Functional (subprocess) tests | Pending | M7 |
| Shutdown lifecycle tests | Pending | M8 |

## Milestones

### M1 — Core connector ✅

Core `DiscordConnector` class with Discord bot, message forwarding to kiso HTTP API, and webhook response server.

- [x] `DiscordConnector` class with config parsing and httpx client
- [x] `build_bot` — Discord client with `on_ready` and `on_message` event handlers
- [x] `_handle_message` — bot/self ignore, channel mapping, prefix filter, empty content guard
- [x] `register_session` — POST /sessions with webhook URL
- [x] `forward_message` — POST /msg, returns JSON body or None
- [x] `deliver_to_discord` — send response to mapped channel with message splitting
- [x] `start_webhook_server` / `stop_webhook_server` — aiohttp lifecycle
- [x] `main()` entry point with asyncio.run

### M2 — Polling fallback ✅

Mandatory fallback when webhook callbacks don't arrive within the timeout window.

- [x] `polling_fallback` — Phase 1: wait 30s for webhook event; Phase 2: poll every 5s
- [x] `poll_status` — GET /status/{session} with `after` parameter
- [x] Stops on `final: true` task
- [x] Skips already-delivered tasks (task_id <= last_task_id)
- [x] Respects `_shutdown` flag for clean exit

### M3 — Session/channel mapping + DM support ✅

Channel-to-session mapping with dynamic DM session creation.

- [x] `map_channel_to_session` — lookup in channel_map dict
- [x] Channel map key normalization (TOML int → str)
- [x] DM detection — `discord.DMChannel` creates `discord-dm-{user_id}` session
- [x] `_register_all_sessions` — registers all configured channels on bot ready
- [x] `PendingSession` dataclass for in-flight tracking

### M4 — Webhook signature verification ✅

HMAC-SHA256 signature verification for webhook callbacks.

- [x] `verify_signature` — compares `X-Kiso-Signature: sha256=<hex>` header
- [x] `handle_callback` — reads raw body when secret is set, verifies before parsing
- [x] Rejects 401 on invalid/missing signature when secret is configured
- [x] Falls back to `request.json()` when no secret is set

### M5 — Test suite ✅

Unit and integration test coverage for all existing functionality.

- [x] `test_config.py` (8 tests) — load_config valid/minimal/missing, load_env valid/missing/empty
- [x] `test_formatting.py` (11 tests) — split_message comprehensive coverage
- [x] `test_session.py` (11 tests) — channel mapping, DM naming, normalization, polling lifecycle
- [x] `test_webhook.py` (13 tests) — parse_webhook, verify_signature, handle_callback
- [x] `test_integration.py` (8 tests, skipped) — live kiso API contract tests
- [x] `conftest.py` — shared fixtures (sample_config, connector, clear_webhook_secret)

### M6 — Missing unit test coverage

New tests to cover `DiscordConnector` methods and edge cases not exercised by the existing suite.

**tests/test_connector.py (new file):**

- [ ] `__init__` defaults — verify `webhook_port=9001`, `kiso_api` trailing slash stripped, `channel_map` normalization, `bot_prefix` default empty
- [ ] `__init__` custom config — non-default webhook_port, webhook_host, webhook_address, bot_prefix
- [ ] `register_session` success (200) — returns True, correct POST body (session, webhook, description)
- [ ] `register_session` success (201) — returns True
- [ ] `register_session` failure (500) — returns False, logs error
- [ ] `register_session` network error — httpx exception, returns False, logs error
- [ ] `forward_message` success (202) — returns JSON body with expected fields
- [ ] `forward_message` failure (400) — returns None, logs error
- [ ] `forward_message` untrusted user — returns JSON with `untrusted: true`
- [ ] `forward_message` network error — httpx exception, returns None
- [ ] `poll_status` success (200) — returns JSON with tasks list
- [ ] `poll_status` failure (404) — returns None, logs warning
- [ ] `poll_status` network error — httpx exception, returns None
- [ ] `deliver_to_discord` no channel mapped — logs error, returns without sending
- [ ] `deliver_to_discord` message exceeds limit — calls split_message, sends multiple parts
- [ ] `deliver_to_discord` discord send failure — logs error, stops sending remaining parts
- [ ] `_handle_message` ignore bot messages — message.author.bot=True, no forwarding
- [ ] `_handle_message` ignore own messages — message.author == bot.user, no forwarding
- [ ] `_handle_message` unmapped channel — non-DM channel not in channel_map, silently ignored
- [ ] `_handle_message` DM creates dynamic session — registers session, maps channel
- [ ] `_handle_message` prefix filtering — message without prefix ignored, with prefix forwarded (prefix stripped)
- [ ] `_handle_message` empty content after prefix strip — ignored
- [ ] `_handle_message` untrusted user response — no PendingSession created, no polling started

**tests/test_webhook.py (additions):**

- [ ] `handle_callback` with webhook secret set but empty `X-Kiso-Signature` header — returns 401
- [ ] `handle_callback` valid signature with content delivery — verify channel.send called with correct content

**tests/test_config.py (addition):**

- [ ] Malformed TOML file (binary garbage) — `load_config` raises `tomllib.TOMLDecodeError` (or exits)

### M7 — Functional tests (subprocess contract)

Start `run.py` as a subprocess and verify early-exit error conditions. These tests validate the entry point contract without mocking.

**tests/test_functional.py (new file):**

- [ ] Missing `config.toml` — exits with code 1, stderr contains "config.toml not found"
- [ ] Missing `KISO_CONNECTOR_DISCORD_BOT_TOKEN` — exits with code 1, stderr contains env var name
- [ ] Missing `KISO_CONNECTOR_DISCORD_KISO_TOKEN` — exits with code 1, stderr contains env var name
- [ ] Both env vars missing — exits with code 1, first missing var reported
- [ ] Corrupt `config.toml` (binary content) — exits with non-zero code

### M8 — SIGTERM/SIGINT graceful shutdown test

Verify the connector shuts down cleanly when receiving termination signals.

**tests/test_shutdown.py (new file):**

- [ ] SIGTERM triggers clean shutdown — `bot.close()` called, webhook server stopped, httpx client closed
- [ ] `shutdown()` sets `_shutdown` flag — polling loops exit
- [ ] `shutdown()` is idempotent — calling twice does not raise

## Milestone Checklist

| Milestone | Status |
|---|---|
| M1 — Core connector | ✅ Done |
| M2 — Polling fallback | ✅ Done |
| M3 — Session/channel mapping + DM support | ✅ Done |
| M4 — Webhook signature verification | ✅ Done |
| M5 — Test suite | ✅ Done |
| M6 — Missing unit test coverage | ⬜ Pending |
| M7 — Functional tests (subprocess contract) | ⬜ Pending |
| M8 — SIGTERM/SIGINT graceful shutdown test | ⬜ Pending |

## Known Issues

- **No reconnection logic** — if the Discord gateway drops the connection, `discord.py` handles reconnection internally, but there is no explicit retry logic for kiso API failures beyond logging.
- **No rate limiting** — rapid messages from Discord are forwarded 1:1 to kiso without throttling.
- **Webhook secret is optional** — when `KISO_CONNECTOR_DISCORD_WEBHOOK_SECRET` is not set, any POST to `/callback` is accepted without authentication.
- **Integration tests require live kiso** — the 8 integration tests in `test_integration.py` are always skipped in CI (no `KISO_TOKEN`).
