"""Tests for configuration loading and environment variable validation."""
import os
import tomllib
from pathlib import Path

import pytest


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


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'webhook_port = 70000\n[channels]\n', "webhook_port"),
        (b'kiso_api = "ftp://localhost"\n[channels]\n', "kiso_api"),
        (b'webhook_address = "http://localhost:9001?x=1"\n[channels]\n', "webhook_address"),
        (b'bot_prefix = 123\n[channels]\n', "bot_prefix"),
        (b'channels = []\n', "[channels]"),
        (b'[channels]\n"abc" = "discord-general"\n', "numeric Discord channel ID"),
        (b'[channels]\n"123" = ""\n', "non-empty session name"),
        (b'[channels]\n"123" = "bad session"\n', "invalid session name"),
    ],
)
def test_load_config_invalid_values(tmp_path, content, expected, caplog):
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(content)

    from run import load_config

    with pytest.raises(SystemExit) as exc_info:
        load_config(config_file)
    assert exc_info.value.code == 1
    assert expected in caplog.text


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


def test_load_config_malformed_toml(tmp_path):
    """Binary garbage should raise a parsing error (TOMLDecodeError or UnicodeDecodeError)."""
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")

    from run import load_config
    with pytest.raises((tomllib.TOMLDecodeError, UnicodeDecodeError)):
        load_config(config_file)
