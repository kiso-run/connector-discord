#!/usr/bin/env python3
"""connector-discord: Discord bridge for kiso.

Forwards Discord messages to the kiso HTTP API and delivers kiso responses
back to Discord channels via webhook callbacks, with a polling fallback.

Entry point: kiso's supervisor runs `.venv/bin/python run.py`
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import discord
import httpx
from aiohttp import web

# ---------------------------------------------------------------------------
# Logging — stdout is captured by kiso's supervisor to connector.log
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
log = logging.getLogger("connector.discord")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.toml"
KISO_DIR = Path.home() / ".kiso"
DISCORD_MAX_LENGTH = 2000
POLLING_WAIT_SECONDS = 30
POLLING_INTERVAL_SECONDS = 5
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25 MB
_SESSION_RE = re.compile(r"^[A-Za-z0-9_@.\-]{1,255}$")


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load config.toml from *path*. Exits with code 1 on failure."""
    if not path.exists():
        log.error(
            "config.toml not found at %s — copy config.example.toml to config.toml",
            path,
        )
        sys.exit(1)
    with open(path, "rb") as fh:
        config = tomllib.load(fh)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    """Validate config.toml shape and fail fast with actionable errors."""
    if not isinstance(config, dict):
        _config_error("config.toml root must be a TOML table")

    channels = config.get("channels", {})
    if not isinstance(channels, dict):
        _config_error("[channels] must be a table of Discord channel ID -> session name")
    for channel_id, session in channels.items():
        channel_text = str(channel_id)
        if not channel_text.isdigit():
            _config_error(f"[channels] key {channel_id!r} must be a numeric Discord channel ID")
        if not isinstance(session, str) or not session.strip():
            _config_error(f"[channels].{channel_text} must map to a non-empty session name")
        if not _SESSION_RE.fullmatch(session.strip()):
            _config_error(
                f"[channels].{channel_text} has invalid session name {session!r} "
                "(allowed: letters, digits, _, -, ., @)"
            )

    webhook_port = config.get("webhook_port", 9001)
    try:
        webhook_port_int = int(webhook_port)
    except (TypeError, ValueError):
        _config_error("webhook_port must be an integer between 1 and 65535")
    if not (1 <= webhook_port_int <= 65535):
        _config_error("webhook_port must be an integer between 1 and 65535")

    webhook_host = config.get("webhook_host", "0.0.0.0")
    if not isinstance(webhook_host, str) or not webhook_host.strip():
        _config_error("webhook_host must be a non-empty string")

    bot_prefix = config.get("bot_prefix", "")
    if not isinstance(bot_prefix, str):
        _config_error("bot_prefix must be a string")

    _validate_http_url("kiso_api", config.get("kiso_api", "http://localhost:8333"))
    _validate_http_url(
        "webhook_address",
        config.get("webhook_address", f"http://localhost:{webhook_port_int}"),
        allow_query=False,
        allow_fragment=False,
    )


def _validate_http_url(
    name: str,
    value: object,
    *,
    allow_query: bool = True,
    allow_fragment: bool = True,
) -> None:
    """Validate that a config value is an http/https URL."""
    if not isinstance(value, str) or not value.strip():
        _config_error(f"{name} must be a non-empty http(s) URL")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _config_error(f"{name} must be a valid http(s) URL")
    if not allow_query and parsed.query:
        _config_error(f"{name} must not include a query string")
    if not allow_fragment and parsed.fragment:
        _config_error(f"{name} must not include a URL fragment")


def _config_error(message: str) -> None:
    """Log a startup config error and exit 1."""
    log.error("Invalid config.toml: %s", message)
    sys.exit(1)


def load_env() -> tuple[str, str]:
    """Load and validate required environment variables. Exits on missing vars."""
    bot_token = os.environ.get("KISO_CONNECTOR_DISCORD_BOT_TOKEN", "")
    kiso_token = os.environ.get("KISO_CONNECTOR_DISCORD_KISO_TOKEN", "")
    if not bot_token:
        log.error("Missing required env var: KISO_CONNECTOR_DISCORD_BOT_TOKEN")
        sys.exit(1)
    if not kiso_token:
        log.error("Missing required env var: KISO_CONNECTOR_DISCORD_KISO_TOKEN")
        sys.exit(1)
    return bot_token, kiso_token


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def split_message(content: str, max_len: int = DISCORD_MAX_LENGTH) -> list[str]:
    """Split *content* to fit within Discord's character limit.

    Splits at paragraph boundaries (\\n\\n) first; falls back to a hard split
    at *max_len* when a single paragraph still exceeds the limit.
    """
    if len(content) <= max_len:
        return [content]

    parts: list[str] = []
    paragraphs = content.split("\n\n")
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                parts.append(current)
            if len(para) > max_len:
                # Hard-split oversized paragraph
                for i in range(0, len(para), max_len):
                    parts.append(para[i : i + max_len])
                current = ""
            else:
                current = para

    if current:
        parts.append(current)

    return parts if parts else [content[:max_len]]


# ---------------------------------------------------------------------------
# Session / channel mapping
# ---------------------------------------------------------------------------

def map_channel_to_session(channel_id: str, channel_map: dict) -> str | None:
    """Return the kiso session name for a Discord channel ID, or None if unmapped."""
    return channel_map.get(channel_id)


# ---------------------------------------------------------------------------
# Webhook payload
# ---------------------------------------------------------------------------

@dataclass
class WebhookPayload:
    session: str
    task_id: int
    type: str
    content: str
    final: bool


def parse_webhook(payload: dict) -> WebhookPayload | None:
    """Parse a kiso webhook payload dict. Returns None if required fields are missing."""
    session = payload.get("session")
    task_type = payload.get("type")
    if not session or not task_type:
        return None
    return WebhookPayload(
        session=session,
        task_id=int(payload.get("task_id", 0)),
        type=task_type,
        content=str(payload.get("content", "")),
        final=bool(payload.get("final", False)),
    )


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify an X-Kiso-Signature: sha256=<hmac-sha256-hex> header."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# In-flight message tracking
# ---------------------------------------------------------------------------

@dataclass
class PendingSession:
    session: str
    last_task_id: int = 0
    webhook_received: asyncio.Event = field(default_factory=asyncio.Event)
    final_received: bool = False


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class DiscordConnector:
    """Bridges Discord ↔ kiso HTTP API."""

    def __init__(self, config: dict, kiso_token: str) -> None:
        self.kiso_api: str = config.get("kiso_api", "http://localhost:8333").rstrip("/")
        self.webhook_port: int = int(config.get("webhook_port", 9001))
        self.webhook_host: str = config.get("webhook_host", "0.0.0.0")
        self.webhook_address: str = config.get(
            "webhook_address", f"http://localhost:{self.webhook_port}"
        ).rstrip("/")
        self.bot_prefix: str = config.get("bot_prefix", "")
        # Normalise channel map keys to strings (TOML integer keys → str)
        self.channel_map: dict[str, str] = {
            str(k): str(v) for k, v in config.get("channels", {}).items()
        }
        self.kiso_token = kiso_token

        # Runtime state
        self.session_to_channel: dict[str, discord.abc.Messageable] = {}
        self.pending: dict[str, PendingSession] = {}
        self.http: httpx.AsyncClient = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.kiso_token}"},
            timeout=10.0,
        )
        self.webhook_runner: web.AppRunner | None = None
        self.bot: discord.Client | None = None
        self._shutdown = False

    # ------------------------------------------------------------------
    # Kiso API
    # ------------------------------------------------------------------

    async def register_session(self, session: str, description: str) -> bool:
        """Register a session with kiso and provide our webhook URL."""
        webhook_url = f"{self.webhook_address}/callback"
        try:
            resp = await self.http.post(
                f"{self.kiso_api}/sessions",
                json={
                    "session": session,
                    "webhook": webhook_url,
                    "description": description,
                },
            )
            if resp.status_code in (200, 201):
                log.info("Session registered: %s", session)
                return True
            log.error(
                "Failed to register session %s: HTTP %s", session, resp.status_code
            )
            return False
        except Exception as exc:
            log.error("Error registering session %s: %s", session, type(exc).__name__)
            return False

    async def forward_message(
        self, session: str, user: str, content: str
    ) -> dict | None:
        """Forward a Discord message to kiso. Returns the 202 JSON body or None."""
        try:
            resp = await self.http.post(
                f"{self.kiso_api}/msg",
                json={"session": session, "user": user, "content": content},
            )
            if resp.status_code == 202:
                return resp.json()
            log.error(
                "POST /msg returned %s for session=%s", resp.status_code, session
            )
            return None
        except Exception as exc:
            log.error(
                "Error forwarding to kiso (session=%s): %s",
                session,
                type(exc).__name__,
            )
            return None

    async def _download_attachments(
        self, session: str, attachments: list[discord.Attachment],
    ) -> list[str]:
        """Download Discord attachments to the session uploads/ directory (M3).

        Returns list of saved filenames. Skips files exceeding MAX_ATTACHMENT_SIZE.
        """
        uploads_dir = KISO_DIR / "sessions" / session / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for att in attachments:
            if att.size > MAX_ATTACHMENT_SIZE:
                log.warning(
                    "Skipping attachment %s (%d bytes, max %d)",
                    att.filename, att.size, MAX_ATTACHMENT_SIZE,
                )
                continue
            # Handle filename collisions
            dest = uploads_dir / att.filename
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                counter = 1
                while dest.exists():
                    dest = uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1
            try:
                data = await att.read()
                dest.write_bytes(data)
                saved.append(dest.name)
                log.info("Saved attachment: %s → %s", att.filename, dest)
            except Exception as exc:
                log.error("Failed to download attachment %s: %s", att.filename, exc)
        return saved

    async def poll_status(self, session: str, after: int = 0) -> dict | None:
        """Poll kiso for pending tasks. Returns status JSON or None on error."""
        try:
            resp = await self.http.get(
                f"{self.kiso_api}/status/{session}",
                params={"after": after},
            )
            if resp.status_code == 200:
                return resp.json()
            log.warning("GET /status/%s returned %s", session, resp.status_code)
            return None
        except Exception as exc:
            log.error(
                "Error polling status (session=%s): %s", session, type(exc).__name__
            )
            return None

    # ------------------------------------------------------------------
    # Response delivery
    # ------------------------------------------------------------------

    async def deliver_to_discord(self, session: str, content: str) -> None:
        """Send a kiso response to the appropriate Discord channel/DM."""
        channel = self.session_to_channel.get(session)
        if channel is None:
            log.error(
                "No Discord channel for session=%s, cannot deliver response", session
            )
            return
        parts = split_message(content)
        for part in parts:
            try:
                await channel.send(part)
            except Exception as exc:
                log.error(
                    "Discord send failed (session=%s): %s",
                    session,
                    type(exc).__name__,
                )
                return
        log.info(
            "Response delivered to Discord (session=%s, %d part(s))",
            session,
            len(parts),
        )

    # ------------------------------------------------------------------
    # Polling fallback (mandatory per spec)
    # ------------------------------------------------------------------

    async def polling_fallback(self, session: str) -> None:
        """Wait up to 30 s for a webhook; if none arrives, poll every 5 s until done."""
        pending = self.pending.get(session)
        if pending is None:
            return

        # Phase 1: wait for the webhook signal
        try:
            await asyncio.wait_for(
                pending.webhook_received.wait(),
                timeout=POLLING_WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.info(
                "No webhook within %ds for session=%s — starting polling fallback",
                POLLING_WAIT_SECONDS,
                session,
            )

        if pending.final_received:
            return  # Already complete via webhook

        # Phase 2: poll until final response or worker idle
        while not pending.final_received and not self._shutdown:
            await asyncio.sleep(POLLING_INTERVAL_SECONDS)
            data = await self.poll_status(session, after=pending.last_task_id)
            if data is None:
                continue

            for task in data.get("tasks", []):
                if task.get("type") != "msg":
                    continue
                task_id = int(task.get("id", 0))
                if task_id > pending.last_task_id:
                    output = str(task.get("output", ""))
                    if output:
                        await self.deliver_to_discord(session, output)
                    pending.last_task_id = task_id
                if task.get("final"):
                    log.info(
                        "Polling complete for session=%s (final task received)", session
                    )
                    pending.final_received = True
                    break

    # ------------------------------------------------------------------
    # Webhook server (receives responses from kiso)
    # ------------------------------------------------------------------

    async def handle_callback(self, request: web.Request) -> web.Response:
        """Handle POST /callback from kiso."""
        webhook_secret = os.environ.get("KISO_CONNECTOR_DISCORD_WEBHOOK_SECRET", "")

        if webhook_secret:
            body = await request.read()
            sig = request.headers.get("X-Kiso-Signature", "")
            if not verify_signature(body, sig, webhook_secret):
                log.warning("Webhook: invalid signature, rejecting request")
                return web.Response(status=401, text="Invalid signature")
            try:
                raw: dict = json.loads(body)
            except ValueError:
                log.warning("Webhook: invalid JSON body")
                return web.Response(status=400, text="Invalid JSON")
        else:
            try:
                raw = await request.json()
            except ValueError:
                log.warning("Webhook: invalid JSON body")
                return web.Response(status=400, text="Invalid JSON")

        payload = parse_webhook(raw)
        if payload is None:
            log.warning("Webhook: missing required fields in payload")
            return web.Response(status=400, text="Missing required fields")

        # Only msg tasks trigger Discord delivery
        if payload.type != "msg":
            return web.Response(status=200, text="ok")

        # Unknown session — return 200 to suppress kiso retries
        if payload.session not in self.session_to_channel:
            log.warning("Webhook: unknown session=%s, ignoring", payload.session)
            return web.Response(status=200, text="ok")

        # Deliver the response to Discord
        if payload.content:
            await self.deliver_to_discord(payload.session, payload.content)

        # Update in-flight tracking
        pending = self.pending.get(payload.session)
        if pending is not None:
            if payload.task_id > pending.last_task_id:
                pending.last_task_id = payload.task_id
            pending.webhook_received.set()
            if payload.final:
                pending.final_received = True
                log.info(
                    "Final response via webhook for session=%s", payload.session
                )

        return web.Response(status=200, text="ok")

    async def start_webhook_server(self) -> None:
        app = web.Application()
        app.router.add_post("/callback", self.handle_callback)
        self.webhook_runner = web.AppRunner(app)
        await self.webhook_runner.setup()
        site = web.TCPSite(self.webhook_runner, self.webhook_host, self.webhook_port)
        await site.start()
        log.info(
            "Webhook server listening on %s:%d", self.webhook_host, self.webhook_port
        )

    async def stop_webhook_server(self) -> None:
        if self.webhook_runner:
            await self.webhook_runner.cleanup()
            log.info("Webhook server stopped")

    # ------------------------------------------------------------------
    # Discord bot
    # ------------------------------------------------------------------

    def build_bot(self) -> discord.Client:
        """Construct and return the Discord client with registered event handlers."""
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        intents.dm_messages = True

        bot = discord.Client(intents=intents)
        self.bot = bot

        @bot.event
        async def on_ready() -> None:
            log.info(
                "Connected to Discord as %s (id=%s)", bot.user, bot.user.id
            )
            await self._register_all_sessions()

        @bot.event
        async def on_message(message: discord.Message) -> None:
            asyncio.create_task(self._handle_message(message))

        return bot

    async def _register_all_sessions(self) -> None:
        """Register all configured channels as kiso sessions on startup."""
        for channel_id, session in self.channel_map.items():
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                log.warning(
                    "Channel %s not found in any joined guild, skipping", channel_id
                )
                continue
            self.session_to_channel[session] = channel
            name = getattr(channel, "name", channel_id)
            await self.register_session(session, f"Discord #{name}")

    async def _handle_message(self, message: discord.Message) -> None:
        """Process an incoming Discord message."""
        # Ignore own messages and other bots
        if message.author == self.bot.user or message.author.bot:
            return

        content = message.content

        # Determine target session
        if isinstance(message.channel, discord.DMChannel):
            user_id = message.author.id
            session = f"discord-dm-{user_id}"
            if session not in self.session_to_channel:
                self.session_to_channel[session] = message.channel
                await self.register_session(
                    session, f"Discord DM with {message.author.name}"
                )
        else:
            channel_id = str(message.channel.id)
            session = map_channel_to_session(channel_id, self.channel_map)
            if session is None:
                return  # Unmapped channel — ignore silently

        # Apply optional prefix filter
        if self.bot_prefix:
            if not content.startswith(self.bot_prefix):
                return
            content = content[len(self.bot_prefix) :].lstrip()

        # M3: download attachments to uploads/
        if message.attachments:
            saved = await self._download_attachments(session, message.attachments)
            if saved:
                content += "\n\n[Uploaded files: " + ", ".join(saved) + "]"

        if not content.strip():
            return

        user = message.author.name
        log.info("Message received: user=%s session=%s", user, session)

        # Show typing indicator while forwarding to kiso
        async with message.channel.typing():
            result = await self.forward_message(session, user, content)

        if result is None:
            return  # Error already logged

        if result.get("untrusted"):
            log.info(
                "User %s is untrusted in session=%s — no response expected",
                user,
                session,
            )
            return

        # Set up in-flight tracking and start polling fallback task
        pending = PendingSession(session=session)
        self.pending[session] = pending
        asyncio.create_task(self.polling_fallback(session))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Gracefully close all connections and stop background servers."""
        self._shutdown = True
        log.info("Shutting down connector...")
        if self.bot:
            await self.bot.close()
        await self.stop_webhook_server()
        await self.http.aclose()
        log.info("Connector shut down cleanly")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    config = load_config()
    bot_token, kiso_token = load_env()

    log.info(
        "Config loaded — %d channel(s) mapped", len(config.get("channels", {}))
    )

    connector = DiscordConnector(config, kiso_token)

    log.info("Starting webhook server...")
    await connector.start_webhook_server()

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _on_signal() -> None:
        log.info("Shutdown signal received")
        shutdown_event.set()

    loop.add_signal_handler(signal.SIGTERM, _on_signal)
    loop.add_signal_handler(signal.SIGINT, _on_signal)

    log.info("Connecting to Discord...")
    bot = connector.build_bot()
    bot_task = asyncio.create_task(bot.start(bot_token))
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        done, _ = await asyncio.wait(
            {bot_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if bot_task in done and not bot_task.cancelled():
            exc = bot_task.exception()
            if exc:
                log.error("Discord bot exited with error: %s", exc)
    finally:
        bot_task.cancel()
        shutdown_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass
        await connector.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
