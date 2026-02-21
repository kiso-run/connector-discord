"""Tests for webhook payload parsing, signature verification, and handler."""
import hashlib
import hmac
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from run import parse_webhook, verify_signature, WebhookPayload


# ---------------------------------------------------------------------------
# parse_webhook
# ---------------------------------------------------------------------------

class TestParseWebhook:
    def test_valid_msg_payload(self):
        payload = {
            "session": "discord-general",
            "task_id": 42,
            "type": "msg",
            "content": "Hello!",
            "final": True,
        }
        result = parse_webhook(payload)

        assert result is not None
        assert isinstance(result, WebhookPayload)
        assert result.session == "discord-general"
        assert result.task_id == 42
        assert result.type == "msg"
        assert result.content == "Hello!"
        assert result.final is True

    def test_final_false(self):
        payload = {
            "session": "s",
            "task_id": 1,
            "type": "msg",
            "content": "partial",
            "final": False,
        }
        result = parse_webhook(payload)
        assert result is not None
        assert result.final is False

    def test_missing_session_returns_none(self):
        payload = {"task_id": 1, "type": "msg", "content": "Hi"}
        assert parse_webhook(payload) is None

    def test_missing_type_returns_none(self):
        payload = {"session": "s", "task_id": 1, "content": "Hi"}
        assert parse_webhook(payload) is None

    def test_missing_optional_fields_have_defaults(self):
        payload = {"session": "s", "type": "msg"}
        result = parse_webhook(payload)

        assert result is not None
        assert result.task_id == 0
        assert result.content == ""
        assert result.final is False

    def test_non_msg_type_is_parsed(self):
        payload = {"session": "s", "type": "exec", "task_id": 5}
        result = parse_webhook(payload)
        assert result is not None
        assert result.type == "exec"

    def test_empty_dict_returns_none(self):
        assert parse_webhook({}) is None


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

class TestVerifySignature:
    def _make_sig(self, body: bytes, secret: str) -> str:
        mac = hmac.new(secret.encode(), body, hashlib.sha256)
        return f"sha256={mac.hexdigest()}"

    def test_valid_signature(self):
        body = b'{"session": "test"}'
        secret = "mysecret"
        sig = self._make_sig(body, secret)
        assert verify_signature(body, sig, secret) is True

    def test_wrong_secret(self):
        body = b'{"session": "test"}'
        sig = self._make_sig(body, "correct-secret")
        assert verify_signature(body, sig, "wrong-secret") is False

    def test_tampered_body(self):
        body = b'{"session": "test"}'
        sig = self._make_sig(body, "secret")
        tampered = b'{"session": "evil"}'
        assert verify_signature(tampered, sig, "secret") is False

    def test_empty_signature(self):
        body = b"data"
        assert verify_signature(body, "", "secret") is False

    def test_malformed_signature(self):
        body = b"data"
        assert verify_signature(body, "not-a-valid-sig", "secret") is False


# ---------------------------------------------------------------------------
# handle_callback (handler integration)
# ---------------------------------------------------------------------------

class TestHandleCallback:
    def _make_request(self, payload: dict, headers: dict | None = None) -> MagicMock:
        """Build a mock aiohttp Request that returns *payload* as JSON."""
        req = MagicMock()
        req.headers = headers or {}
        req.json = AsyncMock(return_value=payload)
        return req

    async def test_valid_msg_delivers_to_discord(self, connector):
        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        req = self._make_request({
            "session": "discord-general",
            "task_id": 1,
            "type": "msg",
            "content": "Hello from kiso!",
            "final": True,
        })

        resp = await connector.handle_callback(req)

        assert resp.status == 200
        mock_channel.send.assert_called_once_with("Hello from kiso!")

    async def test_non_msg_type_ignored(self, connector):
        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        req = self._make_request({
            "session": "discord-general",
            "task_id": 2,
            "type": "exec",
            "content": "internal",
            "final": False,
        })

        resp = await connector.handle_callback(req)

        assert resp.status == 200
        mock_channel.send.assert_not_called()

    async def test_unknown_session_returns_200(self, connector):
        req = self._make_request({
            "session": "unknown-session",
            "task_id": 1,
            "type": "msg",
            "content": "Hi",
            "final": True,
        })

        resp = await connector.handle_callback(req)

        assert resp.status == 200  # 200 to suppress kiso retries

    async def test_invalid_json_returns_400(self, connector):
        req = MagicMock()
        req.headers = {}
        req.json = AsyncMock(side_effect=ValueError("bad json"))

        resp = await connector.handle_callback(req)

        assert resp.status == 400

    async def test_missing_required_fields_returns_400(self, connector):
        req = self._make_request({"task_id": 1, "content": "oops"})  # no session/type

        resp = await connector.handle_callback(req)

        assert resp.status == 400

    async def test_final_sets_pending_final_received(self, connector):
        from run import PendingSession
        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        pending = PendingSession(session="discord-general")
        connector.pending["discord-general"] = pending

        req = self._make_request({
            "session": "discord-general",
            "task_id": 5,
            "type": "msg",
            "content": "Done!",
            "final": True,
        })

        await connector.handle_callback(req)

        assert pending.final_received is True
        assert pending.last_task_id == 5
        assert pending.webhook_received.is_set()

    async def test_webhook_signature_valid(self, connector, monkeypatch):
        """Valid HMAC-SHA256 signature passes through."""
        monkeypatch.setenv("KISO_CONNECTOR_DISCORD_WEBHOOK_SECRET", "supersecret")

        mock_channel = AsyncMock()
        connector.session_to_channel["discord-general"] = mock_channel

        body = b'{"session":"discord-general","task_id":1,"type":"msg","content":"Hi","final":true}'
        mac = hmac.new(b"supersecret", body, hashlib.sha256)
        sig = f"sha256={mac.hexdigest()}"

        req = MagicMock()
        req.headers = {"X-Kiso-Signature": sig}
        req.read = AsyncMock(return_value=body)

        resp = await connector.handle_callback(req)

        assert resp.status == 200
        mock_channel.send.assert_called_once_with("Hi")

    async def test_webhook_signature_invalid(self, connector, monkeypatch):
        """Invalid HMAC signature is rejected with 401."""
        monkeypatch.setenv("KISO_CONNECTOR_DISCORD_WEBHOOK_SECRET", "supersecret")

        body = b'{"session":"discord-general","task_id":1,"type":"msg","content":"Hi","final":true}'
        req = MagicMock()
        req.headers = {"X-Kiso-Signature": "sha256=deadbeef"}
        req.read = AsyncMock(return_value=body)

        resp = await connector.handle_callback(req)

        assert resp.status == 401

    async def test_webhook_missing_signature_when_secret_configured(
        self, connector, monkeypatch
    ):
        """Missing signature header when secret is configured → rejected."""
        monkeypatch.setenv("KISO_CONNECTOR_DISCORD_WEBHOOK_SECRET", "supersecret")

        body = b'{"session":"discord-general","task_id":1,"type":"msg","content":"Hi","final":true}'
        req = MagicMock()
        req.headers = {}
        req.read = AsyncMock(return_value=body)

        resp = await connector.handle_callback(req)

        assert resp.status == 401
