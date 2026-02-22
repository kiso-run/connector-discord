"""Integration tests for connector-discord.

These tests require a running Kiso instance. Set the environment variables:
  KISO_API      - Kiso API base URL (default: http://localhost:8333)
  KISO_TOKEN    - Bearer token for the Kiso API

Run with:
  uv run --group dev pytest tests/test_integration.py -v
"""
import os
import pytest
import httpx

KISO_API = os.environ.get("KISO_API", "http://localhost:8333")
KISO_TOKEN = os.environ.get("KISO_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not KISO_TOKEN,
    reason="KISO_TOKEN not set — skipping integration tests",
)


@pytest.fixture(scope="module")
def kiso_client():
    with httpx.Client(
        base_url=KISO_API,
        headers={"Authorization": f"Bearer {KISO_TOKEN}"},
        timeout=10.0,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Session registration
# ---------------------------------------------------------------------------

class TestSessionRegistration:
    def test_register_session_returns_200_or_201(self, kiso_client):
        resp = kiso_client.post(
            "/sessions",
            json={
                "session": "discord-integration-test",
                "webhook": "http://localhost:19999/callback",
                "description": "Integration test session",
            },
        )
        assert resp.status_code in (200, 201)

    def test_register_session_idempotent(self, kiso_client):
        payload = {
            "session": "discord-integration-test",
            "webhook": "http://localhost:19999/callback",
            "description": "Integration test session",
        }
        r1 = kiso_client.post("/sessions", json=payload)
        r2 = kiso_client.post("/sessions", json=payload)
        assert r1.status_code in (200, 201)
        assert r2.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Message forwarding
# ---------------------------------------------------------------------------

class TestMessageForwarding:
    def test_send_message_returns_202(self, kiso_client):
        resp = kiso_client.post(
            "/msg",
            json={
                "session": "discord-integration-test",
                "user": "TestUser#0001",
                "content": "hello from integration test",
            },
        )
        assert resp.status_code == 202

    def test_send_message_untrusted_user(self, kiso_client):
        """Unknown users get queued=false, untrusted=true — not an error."""
        resp = kiso_client.post(
            "/msg",
            json={
                "session": "discord-integration-test",
                "user": "nobody_unknown_xyz#9999",
                "content": "test",
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body.get("untrusted") is True

    def test_send_message_invalid_token_returns_401(self):
        with httpx.Client(
            base_url=KISO_API,
            headers={"Authorization": "Bearer invalid-token-xyz"},
            timeout=10.0,
        ) as bad_client:
            resp = bad_client.post(
                "/msg",
                json={
                    "session": "discord-integration-test",
                    "user": "user",
                    "content": "test",
                },
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------

class TestStatusPolling:
    def test_poll_status_returns_200(self, kiso_client):
        resp = kiso_client.get("/status/discord-integration-test")
        assert resp.status_code == 200

    def test_poll_status_with_after_param(self, kiso_client):
        resp = kiso_client.get("/status/discord-integration-test", params={"after": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert "tasks" in body

    def test_poll_status_tasks_have_expected_fields(self, kiso_client):
        resp = kiso_client.get("/status/discord-integration-test", params={"after": 0})
        assert resp.status_code == 200
        for task in resp.json().get("tasks", []):
            assert "id" in task
            assert "type" in task
