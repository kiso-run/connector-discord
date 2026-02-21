"""Shared pytest fixtures for connector-discord tests."""
import pytest


@pytest.fixture
def sample_config() -> dict:
    return {
        "kiso_api": "http://localhost:8333",
        "webhook_port": 9001,
        "webhook_host": "0.0.0.0",
        "bot_prefix": "",
        "channels": {
            "123456789012345678": "discord-general",
            "987654321098765432": "discord-dev",
        },
    }


@pytest.fixture
def connector(sample_config):
    from run import DiscordConnector
    return DiscordConnector(sample_config, kiso_token="test-kiso-token")


@pytest.fixture(autouse=True)
def clear_webhook_secret(monkeypatch):
    """Ensure no webhook secret is set unless a test explicitly configures one."""
    monkeypatch.delenv("KISO_CONNECTOR_DISCORD_WEBHOOK_SECRET", raising=False)
