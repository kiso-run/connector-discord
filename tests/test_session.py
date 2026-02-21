"""Tests for session/channel mapping and polling fallback logic."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from run import map_channel_to_session


# ---------------------------------------------------------------------------
# Channel → session mapping
# ---------------------------------------------------------------------------

class TestChannelMapping:
    def test_mapped_channel_returns_session(self):
        channel_map = {
            "123456789012345678": "discord-general",
            "987654321098765432": "discord-dev",
        }
        assert map_channel_to_session("123456789012345678", channel_map) == "discord-general"
        assert map_channel_to_session("987654321098765432", channel_map) == "discord-dev"

    def test_unmapped_channel_returns_none(self):
        channel_map = {"123": "discord-general"}
        assert map_channel_to_session("999", channel_map) is None

    def test_empty_channel_map_returns_none(self):
        assert map_channel_to_session("123", {}) is None

    def test_dm_session_naming_convention(self):
        """DM sessions must follow the discord-dm-{user_id} pattern."""
        user_id = 123456789012345678
        session = f"discord-dm-{user_id}"
        assert session == "discord-dm-123456789012345678"
        # Must match kiso session ID pattern: ^[a-zA-Z0-9_@.\-]{1,255}$
        import re
        assert re.match(r'^[a-zA-Z0-9_@.\-]{1,255}$', session)

    def test_session_name_matches_kiso_pattern(self):
        """All session names from the channel map must match kiso's ID pattern."""
        import re
        channel_map = {
            "1": "discord-general",
            "2": "discord-dev",
            "3": "my_session.name",
        }
        pattern = re.compile(r'^[a-zA-Z0-9_@.\-]{1,255}$')
        for session in channel_map.values():
            assert pattern.match(session), f"Session {session!r} does not match pattern"


# ---------------------------------------------------------------------------
# DiscordConnector channel map normalisation
# ---------------------------------------------------------------------------

class TestConnectorChannelMap:
    def test_channel_map_keys_normalised_to_str(self, sample_config):
        """Integer keys from TOML must be normalised to strings."""
        config = dict(sample_config)
        config["channels"] = {123: "discord-general"}  # int key

        from run import DiscordConnector
        c = DiscordConnector(config, kiso_token="tok")

        assert "123" in c.channel_map
        assert c.channel_map["123"] == "discord-general"

    def test_empty_channels(self, sample_config):
        config = dict(sample_config)
        config["channels"] = {}

        from run import DiscordConnector
        c = DiscordConnector(config, kiso_token="tok")

        assert c.channel_map == {}


# ---------------------------------------------------------------------------
# Polling fallback
# ---------------------------------------------------------------------------

class TestPollingFallback:
    async def test_polling_stops_on_final_true(self, connector):
        """Polling loop exits when worker is idle."""
        from run import PendingSession

        pending = PendingSession(session="discord-general")
        connector.pending["discord-general"] = pending

        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        call_count = 0

        async def mock_poll(session, after=0):
            nonlocal call_count
            call_count += 1
            return {
                "tasks": [
                    {"id": call_count, "type": "msg", "output": f"Response {call_count}"}
                ],
                "worker_running": call_count < 2,
                "active_task": None,
                "queue_length": 0,
            }

        connector.poll_status = mock_poll

        # Trigger timeout immediately (no webhook received)
        await asyncio.wait_for(
            connector.polling_fallback("discord-general"),
            timeout=5.0,
        )

        assert pending.final_received is True
        assert mock_channel.send.call_count >= 1

    async def test_polling_skips_already_delivered_tasks(self, connector):
        """Tasks with id <= last_task_id must not be re-delivered."""
        from run import PendingSession

        pending = PendingSession(session="discord-general", last_task_id=10)
        connector.pending["discord-general"] = pending

        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        async def mock_poll(session, after=0):
            return {
                "tasks": [
                    {"id": 5, "type": "msg", "output": "Old"},
                    {"id": 10, "type": "msg", "output": "Old too"},
                ],
                "worker_running": False,
                "active_task": None,
                "queue_length": 0,
            }

        connector.poll_status = mock_poll

        await asyncio.wait_for(
            connector.polling_fallback("discord-general"),
            timeout=5.0,
        )

        # No messages should be delivered (all task_ids <= last_task_id=10)
        mock_channel.send.assert_not_called()

    async def test_webhook_received_before_timeout_skips_polling(self, connector):
        """If webhook arrives before 30s timeout, polling phase is skipped."""
        from run import PendingSession

        pending = PendingSession(session="discord-general")
        pending.final_received = True  # Webhook delivered everything
        connector.pending["discord-general"] = pending

        poll_called = False

        async def mock_poll(session, after=0):
            nonlocal poll_called
            poll_called = True
            return None

        connector.poll_status = mock_poll

        # Signal webhook immediately
        pending.webhook_received.set()

        await asyncio.wait_for(
            connector.polling_fallback("discord-general"),
            timeout=5.0,
        )

        assert not poll_called

    async def test_polling_updates_last_task_id(self, connector):
        """last_task_id must advance with each delivered task."""
        from run import PendingSession

        pending = PendingSession(session="discord-general")
        connector.pending["discord-general"] = pending

        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        responses = [
            {
                "tasks": [{"id": 1, "type": "msg", "output": "First"}],
                "worker_running": True,
                "active_task": {"id": 2},
                "queue_length": 0,
            },
            {
                "tasks": [{"id": 2, "type": "msg", "output": "Second"}],
                "worker_running": False,
                "active_task": None,
                "queue_length": 0,
            },
        ]
        iter_responses = iter(responses)

        async def mock_poll(session, after=0):
            try:
                return next(iter_responses)
            except StopIteration:
                return {
                    "tasks": [],
                    "worker_running": False,
                    "active_task": None,
                    "queue_length": 0,
                }

        connector.poll_status = mock_poll

        await asyncio.wait_for(
            connector.polling_fallback("discord-general"),
            timeout=5.0,
        )

        assert pending.last_task_id == 2
        assert mock_channel.send.call_count == 2
