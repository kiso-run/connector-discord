"""Tests for configuration loading and environment variable validation."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


def test_load_config_valid(tmp_path):
    config_content = b"""
kiso_api = "http://localhost:8333"
webhook_port = 9001
webhook_host = "0.0.0.0"
bot_prefix = ""

[channels]
"123456789012345678" = "discord-general"
"""
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(config_content)

    from run import load_config
    config = load_config(config_file)

    assert config["kiso_api"] == "http://localhost:8333"
    assert config["webhook_port"] == 9001
    assert config["webhook_host"] == "0.0.0.0"
    assert config["bot_prefix"] == ""
    assert config["channels"] == {"123456789012345678": "discord-general"}


def test_load_config_minimal(tmp_path):
    """Config with only channels section is valid."""
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b"[channels]\n")

    from run import load_config
    config = load_config(config_file)
    assert config.get("channels") == {}


def test_load_config_missing_file(tmp_path):
    from run import load_config
    with pytest.raises(SystemExit) as exc_info:
        load_config(tmp_path / "nonexistent.toml")
    assert exc_info.value.code == 1


def test_load_env_valid(monkeypatch):
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_BOT_TOKEN", "bot-tok-abc")
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_KISO_TOKEN", "kiso-tok-xyz")

    from run import load_env
    bot_token, kiso_token = load_env()
    assert bot_token == "bot-tok-abc"
    assert kiso_token == "kiso-tok-xyz"


def test_load_env_missing_bot_token(monkeypatch):
    monkeypatch.delenv("KISO_CONNECTOR_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_KISO_TOKEN", "kiso-tok")

    from run import load_env
    with pytest.raises(SystemExit) as exc_info:
        load_env()
    assert exc_info.value.code == 1


def test_load_env_missing_kiso_token(monkeypatch):
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_BOT_TOKEN", "bot-tok")
    monkeypatch.delenv("KISO_CONNECTOR_DISCORD_KISO_TOKEN", raising=False)

    from run import load_env
    with pytest.raises(SystemExit) as exc_info:
        load_env()
    assert exc_info.value.code == 1


def test_load_env_empty_bot_token(monkeypatch):
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_BOT_TOKEN", "")
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_KISO_TOKEN", "kiso-tok")

    from run import load_env
    with pytest.raises(SystemExit):
        load_env()


def test_load_env_empty_kiso_token(monkeypatch):
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_BOT_TOKEN", "bot-tok")
    monkeypatch.setenv("KISO_CONNECTOR_DISCORD_KISO_TOKEN", "")

    from run import load_env
    with pytest.raises(SystemExit):
        load_env()
