"""Tests for graceful shutdown lifecycle (M8)."""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestShutdown:
    async def test_shutdown_closes_resources(self, connector):
        """shutdown() cleans up httpx client, bot, and webhook server."""
        connector.http = AsyncMock()
        connector.bot = AsyncMock()
        connector.webhook_runner = AsyncMock()

        await connector.shutdown()

        connector.bot.close.assert_called_once()
        connector.webhook_runner.cleanup.assert_called_once()
        connector.http.aclose.assert_called_once()
        assert connector._shutdown is True

    async def test_shutdown_sets_shutdown_flag(self, connector):
        """shutdown() sets _shutdown flag so polling loops exit."""
        connector.http = AsyncMock()
        connector.bot = None
        connector.webhook_runner = None

        await connector.shutdown()

        assert connector._shutdown is True
        connector.http.aclose.assert_called_once()

    async def test_shutdown_idempotent(self, connector):
        """Calling shutdown() twice does not raise."""
        connector.http = AsyncMock()
        connector.bot = AsyncMock()
        connector.webhook_runner = AsyncMock()

        await connector.shutdown()
        assert connector._shutdown is True

        # Second call should not raise
        await connector.shutdown()
        assert connector._shutdown is True

    async def test_shutdown_without_bot(self, connector):
        """shutdown() works when bot was never created."""
        connector.http = AsyncMock()
        connector.bot = None
        connector.webhook_runner = None

        await connector.shutdown()

        connector.http.aclose.assert_called_once()

    async def test_shutdown_without_webhook_runner(self, connector):
        """shutdown() works when webhook server was never started."""
        connector.http = AsyncMock()
        connector.bot = AsyncMock()
        connector.webhook_runner = None

        await connector.shutdown()

        connector.bot.close.assert_called_once()
        connector.http.aclose.assert_called_once()
