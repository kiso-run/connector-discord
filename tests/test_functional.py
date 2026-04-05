"""Functional tests — subprocess contract validation (M7).

These tests run run.py as a subprocess and verify early-exit error conditions
without mocking.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

RUN_PY = str(Path(__file__).parent.parent / "run.py")
PLUGIN_DIR = str(Path(__file__).parent.parent)


def _run(env: dict, timeout: int = 5) -> subprocess.CompletedProcess:
    """Run run.py as subprocess with the given environment overlay."""
    base_env = {"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"}
    base_env.update(env)
    return subprocess.run(
        [sys.executable, RUN_PY],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=PLUGIN_DIR,
        timeout=timeout,
    )


class TestMissingEnvVars:
    """Config exists (next to run.py may or may not exist), but env vars are missing."""

    def test_missing_bot_token(self, tmp_path):
        """Missing BOT_TOKEN exits 1 with error message."""
        # Create a valid config.toml next to a copy of run.py
        config = tmp_path / "config.toml"
        config.write_text('[channels]\n"123" = "test"\n')

        # Copy run.py to tmp_path so CONFIG_PATH resolves to our config
        run_copy = tmp_path / "run.py"
        run_copy.write_text(Path(RUN_PY).read_text())

        result = subprocess.run(
            [sys.executable, str(run_copy)],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": "/tmp",
                "KISO_CONNECTOR_DISCORD_KISO_TOKEN": "valid-token",
                # BOT_TOKEN intentionally missing
            },
            cwd=str(tmp_path),
            timeout=5,
        )

        assert result.returncode == 1
        assert "KISO_CONNECTOR_DISCORD_BOT_TOKEN" in result.stdout

    def test_missing_kiso_token(self, tmp_path):
        """Missing KISO_TOKEN exits 1 with error message."""
        config = tmp_path / "config.toml"
        config.write_text('[channels]\n"123" = "test"\n')

        run_copy = tmp_path / "run.py"
        run_copy.write_text(Path(RUN_PY).read_text())

        result = subprocess.run(
            [sys.executable, str(run_copy)],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": "/tmp",
                "KISO_CONNECTOR_DISCORD_BOT_TOKEN": "valid-bot-token",
                # KISO_TOKEN intentionally missing
            },
            cwd=str(tmp_path),
            timeout=5,
        )

        assert result.returncode == 1
        assert "KISO_CONNECTOR_DISCORD_KISO_TOKEN" in result.stdout

    def test_both_tokens_missing(self, tmp_path):
        """Both missing — first missing var (BOT_TOKEN) reported."""
        config = tmp_path / "config.toml"
        config.write_text('[channels]\n"123" = "test"\n')

        run_copy = tmp_path / "run.py"
        run_copy.write_text(Path(RUN_PY).read_text())

        result = subprocess.run(
            [sys.executable, str(run_copy)],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": "/tmp",
            },
            cwd=str(tmp_path),
            timeout=5,
        )

        assert result.returncode == 1
        assert "KISO_CONNECTOR_DISCORD_BOT_TOKEN" in result.stdout

    def test_missing_config(self, tmp_path):
        """Missing config.toml exits 1."""
        # Copy run.py to a directory without config.toml
        run_copy = tmp_path / "run.py"
        run_copy.write_text(Path(RUN_PY).read_text())

        result = subprocess.run(
            [sys.executable, str(run_copy)],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": "/tmp",
            },
            cwd=str(tmp_path),
            timeout=5,
        )

        assert result.returncode == 1
        assert "config.toml" in result.stdout

    def test_corrupt_config(self, tmp_path):
        """Corrupt config.toml exits with non-zero code."""
        config = tmp_path / "config.toml"
        config.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")

        run_copy = tmp_path / "run.py"
        run_copy.write_text(Path(RUN_PY).read_text())

        result = subprocess.run(
            [sys.executable, str(run_copy)],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": "/tmp",
            },
            cwd=str(tmp_path),
            timeout=5,
        )

        assert result.returncode != 0

    def test_invalid_config_fails_fast(self, tmp_path):
        """Invalid config.toml exits 1 with a config validation error."""
        config = tmp_path / "config.toml"
        config.write_text('webhook_port = 70000\n[channels]\n')

        run_copy = tmp_path / "run.py"
        run_copy.write_text(Path(RUN_PY).read_text())

        result = subprocess.run(
            [sys.executable, str(run_copy)],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": "/tmp",
                "KISO_CONNECTOR_DISCORD_BOT_TOKEN": "valid-bot-token",
                "KISO_CONNECTOR_DISCORD_KISO_TOKEN": "valid-token",
            },
            cwd=str(tmp_path),
            timeout=5,
        )

        assert result.returncode == 1
        assert "Invalid config.toml" in result.stdout
