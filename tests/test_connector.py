"""Tests for DiscordConnector methods — M6 coverage."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import httpx

from run import DiscordConnector, split_message


# ---------------------------------------------------------------------------
# __init__ defaults and custom config
# ---------------------------------------------------------------------------

class TestConnectorInit:
    def test_defaults(self, sample_config):
        c = DiscordConnector(sample_config, kiso_token="tok")

        assert c.kiso_api == "http://localhost:8333"
        assert c.webhook_port == 9001
        assert c.bot_prefix == ""
        assert isinstance(c.channel_map, dict)
        # Keys must be strings
        for k in c.channel_map:
            assert isinstance(k, str)

    def test_kiso_api_trailing_slash_stripped(self):
        config = {"kiso_api": "http://example.com/api/"}
        c = DiscordConnector(config, kiso_token="tok")
        assert c.kiso_api == "http://example.com/api"

    def test_channel_map_int_keys_normalised(self):
        config = {"channels": {123: "sess-a", 456: "sess-b"}}
        c = DiscordConnector(config, kiso_token="tok")
        assert "123" in c.channel_map
        assert "456" in c.channel_map

    def test_custom_config(self):
        config = {
            "kiso_api": "https://kiso.example.com",
            "webhook_port": 5555,
            "webhook_host": "127.0.0.1",
            "webhook_address": "https://public.example.com:5555",
            "bot_prefix": "!kiso ",
            "channels": {},
        }
        c = DiscordConnector(config, kiso_token="my-token")

        assert c.webhook_port == 5555
        assert c.webhook_host == "127.0.0.1"
        assert c.webhook_address == "https://public.example.com:5555"
        assert c.bot_prefix == "!kiso "
        assert c.kiso_token == "my-token"


# ---------------------------------------------------------------------------
# register_session
# ---------------------------------------------------------------------------

class TestRegisterSession:
    async def test_success_200(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        connector.http.post = AsyncMock(return_value=mock_resp)

        result = await connector.register_session("my-session", "Test channel")

        assert result is True
        connector.http.post.assert_called_once()
        call_kwargs = connector.http.post.call_args
        assert "sessions" in call_kwargs.args[0]

    async def test_success_201(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        connector.http.post = AsyncMock(return_value=mock_resp)

        result = await connector.register_session("new-session", "New channel")
        assert result is True

    async def test_failure_500(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        connector.http.post = AsyncMock(return_value=mock_resp)

        result = await connector.register_session("fail-session", "desc")
        assert result is False

    async def test_network_error(self, connector):
        connector.http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await connector.register_session("err-session", "desc")
        assert result is False

    async def test_post_body_contains_webhook_url(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        connector.http.post = AsyncMock(return_value=mock_resp)

        await connector.register_session("sess", "desc")

        call_kwargs = connector.http.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["session"] == "sess"
        assert body["description"] == "desc"
        assert "/callback" in body["webhook"]


# ---------------------------------------------------------------------------
# forward_message
# ---------------------------------------------------------------------------

class TestForwardMessage:
    async def test_success_202(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"ok": True, "plan_id": 1}
        connector.http.post = AsyncMock(return_value=mock_resp)

        result = await connector.forward_message("sess", "alice", "hello")

        assert result == {"ok": True, "plan_id": 1}

    async def test_failure_400(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        connector.http.post = AsyncMock(return_value=mock_resp)

        result = await connector.forward_message("sess", "alice", "hello")
        assert result is None

    async def test_untrusted_user(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"untrusted": True}
        connector.http.post = AsyncMock(return_value=mock_resp)

        result = await connector.forward_message("sess", "stranger", "hello")
        assert result == {"untrusted": True}

    async def test_network_error(self, connector):
        connector.http.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await connector.forward_message("sess", "alice", "hello")
        assert result is None


# ---------------------------------------------------------------------------
# poll_status
# ---------------------------------------------------------------------------

class TestPollStatus:
    async def test_success_200(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tasks": [{"id": 1, "type": "msg"}]}
        connector.http.get = AsyncMock(return_value=mock_resp)

        result = await connector.poll_status("sess", after=0)
        assert result == {"tasks": [{"id": 1, "type": "msg"}]}

    async def test_failure_404(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        connector.http.get = AsyncMock(return_value=mock_resp)

        result = await connector.poll_status("sess")
        assert result is None

    async def test_network_error(self, connector):
        connector.http.get = AsyncMock(
            side_effect=httpx.ReadTimeout("timeout")
        )

        result = await connector.poll_status("sess")
        assert result is None


# ---------------------------------------------------------------------------
# deliver_to_discord
# ---------------------------------------------------------------------------

class TestDeliverToDiscord:
    async def test_no_channel_mapped(self, connector):
        """Should log error and not crash when session has no channel."""
        # session_to_channel is empty by default
        await connector.deliver_to_discord("unknown-session", "Hello")
        # No exception raised — success

    async def test_content_needs_splitting(self, connector):
        """Long content should be split into multiple sends."""
        mock_channel = AsyncMock()
        connector.session_to_channel["sess"] = mock_channel

        # Create content longer than 2000 chars
        long_content = "A" * 2500
        await connector.deliver_to_discord("sess", long_content)

        assert mock_channel.send.call_count >= 2

    async def test_discord_send_failure_stops(self, connector):
        """If channel.send raises, remaining parts are not sent."""
        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock(side_effect=Exception("Discord API error"))
        connector.session_to_channel["sess"] = mock_channel

        long_content = "A" * 2500
        # Should not raise
        await connector.deliver_to_discord("sess", long_content)
        # Only one call attempted before error stops the loop
        assert mock_channel.send.call_count == 1

    async def test_normal_delivery(self, connector):
        mock_channel = AsyncMock()
        connector.session_to_channel["sess"] = mock_channel

        await connector.deliver_to_discord("sess", "Hello world")

        mock_channel.send.assert_called_once_with("Hello world")


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------

class TestHandleMessage:
    def _make_message(
        self,
        content="hello",
        author_bot=False,
        author_is_self=False,
        channel_id=123456789012345678,
        is_dm=False,
        author_name="testuser",
        author_id=999,
    ):
        msg = MagicMock()
        msg.author = MagicMock()
        msg.author.bot = author_bot
        msg.author.name = author_name
        msg.author.id = author_id
        msg.content = content

        if is_dm:
            msg.channel = MagicMock(spec=discord.DMChannel)
        else:
            msg.channel = MagicMock()
        msg.channel.id = channel_id

        # Make typing() work as async context manager
        typing_ctx = MagicMock()
        typing_ctx.__aenter__ = AsyncMock(return_value=None)
        typing_ctx.__aexit__ = AsyncMock(return_value=None)
        msg.channel.typing = MagicMock(return_value=typing_ctx)

        return msg

    async def test_ignore_bot_messages(self, connector):
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()
        connector.http.post = AsyncMock()

        msg = self._make_message(author_bot=True)
        await connector._handle_message(msg)

        connector.http.post.assert_not_called()

    async def test_ignore_own_messages(self, connector):
        bot_user = MagicMock()
        connector.bot = MagicMock()
        connector.bot.user = bot_user

        msg = self._make_message()
        msg.author = bot_user  # author IS the bot

        connector.http.post = AsyncMock()
        await connector._handle_message(msg)

        connector.http.post.assert_not_called()

    async def test_unmapped_channel_ignored(self, connector):
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()
        connector.http.post = AsyncMock()

        msg = self._make_message(channel_id=111111111111111111)
        await connector._handle_message(msg)

        connector.http.post.assert_not_called()

    async def test_prefix_filtering_no_prefix(self, connector):
        """Message without the required prefix is ignored."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()
        connector.bot_prefix = "!kiso "
        connector.http.post = AsyncMock()

        msg = self._make_message(content="hello world")
        await connector._handle_message(msg)

        connector.http.post.assert_not_called()

    async def test_prefix_filtering_with_prefix(self, connector):
        """Message with prefix is forwarded, prefix stripped."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()
        connector.bot_prefix = "!kiso "

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"untrusted": True}
        connector.http.post = AsyncMock(return_value=mock_resp)

        msg = self._make_message(content="!kiso what is python?")
        await connector._handle_message(msg)

        call_kwargs = connector.http.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["content"] == "what is python?"

    async def test_empty_content_ignored(self, connector):
        """Empty content after stripping should be ignored."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()
        connector.http.post = AsyncMock()

        msg = self._make_message(content="   ")
        await connector._handle_message(msg)

        connector.http.post.assert_not_called()

    async def test_empty_content_after_prefix_strip(self, connector):
        """Only prefix with no actual content should be ignored."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()
        connector.bot_prefix = "!kiso "
        connector.http.post = AsyncMock()

        msg = self._make_message(content="!kiso    ")
        await connector._handle_message(msg)

        connector.http.post.assert_not_called()

    async def test_dm_creates_dynamic_session(self, connector):
        """DM messages should create a dynamic session."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()

        # register_session mock
        register_mock = AsyncMock(return_value=True)
        connector.register_session = register_mock

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"untrusted": True}
        connector.http.post = AsyncMock(return_value=mock_resp)

        msg = self._make_message(is_dm=True, author_id=42, author_name="bob")
        await connector._handle_message(msg)

        # Should have registered the DM session
        register_mock.assert_called_once()
        call_args = register_mock.call_args
        assert call_args.args[0] == "discord-dm-42"
        assert "bob" in call_args.args[1]

    async def test_untrusted_user_no_pending(self, connector):
        """Untrusted user response should not create PendingSession."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"untrusted": True}
        connector.http.post = AsyncMock(return_value=mock_resp)

        msg = self._make_message()
        await connector._handle_message(msg)

        # No pending session should be created for untrusted users
        session = connector.channel_map.get(str(msg.channel.id))
        if session:
            assert session not in connector.pending

    async def test_trusted_user_creates_pending(self, connector):
        """Trusted user response should create PendingSession and start polling."""
        connector.bot = MagicMock()
        connector.bot.user = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"ok": True}
        connector.http.post = AsyncMock(return_value=mock_resp)

        # Mock polling_fallback to avoid actual polling
        connector.polling_fallback = AsyncMock()

        msg = self._make_message()
        await connector._handle_message(msg)

        session = connector.channel_map.get(str(msg.channel.id))
        assert session is not None
        assert session in connector.pending


# ---------------------------------------------------------------------------
# _download_attachments (M3)
# ---------------------------------------------------------------------------

class TestDownloadAttachments:
    def _make_attachment(self, filename="image.png", size=1024, data=b"fakedata"):
        att = MagicMock(spec=["filename", "size", "read"])
        att.filename = filename
        att.size = size
        att.read = AsyncMock(return_value=data)
        return att

    @pytest.mark.asyncio
    async def test_downloads_single_attachment(self, connector, tmp_path):
        with patch("run.KISO_DIR", tmp_path):
            att = self._make_attachment()
            saved = await connector._download_attachments("test-sess", [att])
        assert saved == ["image.png"]
        assert (tmp_path / "sessions" / "test-sess" / "uploads" / "image.png").exists()
        assert (tmp_path / "sessions" / "test-sess" / "uploads" / "image.png").read_bytes() == b"fakedata"

    @pytest.mark.asyncio
    async def test_skips_oversized_attachment(self, connector, tmp_path):
        with patch("run.KISO_DIR", tmp_path):
            att = self._make_attachment(size=30 * 1024 * 1024)  # 30 MB
            saved = await connector._download_attachments("test-sess", [att])
        assert saved == []
        att.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_filename_collision(self, connector, tmp_path):
        with patch("run.KISO_DIR", tmp_path):
            uploads = tmp_path / "sessions" / "test-sess" / "uploads"
            uploads.mkdir(parents=True)
            (uploads / "file.txt").write_text("existing")
            att = self._make_attachment(filename="file.txt", data=b"new")
            saved = await connector._download_attachments("test-sess", [att])
        assert saved == ["file_1.txt"]
        assert (uploads / "file_1.txt").read_bytes() == b"new"
        assert (uploads / "file.txt").read_text() == "existing"

    @pytest.mark.asyncio
    async def test_multiple_attachments(self, connector, tmp_path):
        with patch("run.KISO_DIR", tmp_path):
            att1 = self._make_attachment(filename="a.png", data=b"aaa")
            att2 = self._make_attachment(filename="b.pdf", data=b"bbb")
            saved = await connector._download_attachments("test-sess", [att1, att2])
        assert len(saved) == 2
        assert "a.png" in saved
        assert "b.pdf" in saved

    @pytest.mark.asyncio
    async def test_download_error_skips_file(self, connector, tmp_path):
        with patch("run.KISO_DIR", tmp_path):
            att = self._make_attachment()
            att.read = AsyncMock(side_effect=Exception("network error"))
            saved = await connector._download_attachments("test-sess", [att])
        assert saved == []
