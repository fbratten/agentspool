"""Tests for relay server endpoints using FastAPI TestClient."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent_comm.message_types import MessageV2
from agent_comm.relay.auth import HMACSigner, SecretStore
from agent_comm.relay.config import RelayConfig
from agent_comm.relay.server import create_app


class TestRelayServer(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = RelayConfig(
            project_root=Path(self.tmpdir),
            secrets_path=Path(self.tmpdir) / "secrets.json",
            db_path=Path(self.tmpdir) / "relay.db",
            registry_path=Path(self.tmpdir) / "registry.json",
        )
        self.app = create_app(self.config)
        self.client = TestClient(self.app)

        # Generate a secret for the test agent
        self.secret_store = self.app.state.secrets
        self.agent_secret = os.urandom(32)
        self.secret_store.set("test-agent", self.agent_secret)

    def _auth_headers_body(self, body: str) -> dict:
        """Generate HMAC auth headers for a body-based request."""
        timestamp = str(time.time())
        nonce = str(os.urandom(16).hex())
        payload = HMACSigner.build_body_payload(body, timestamp, nonce)
        sig = HMACSigner.sign(self.agent_secret, payload)
        return {
            "X-Agent-ID": "test-agent",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sig,
            "Content-Type": "application/json",
        }

    def _auth_headers_action(self, action: str) -> dict:
        """Generate HMAC auth headers for an action-based request."""
        timestamp = str(time.time())
        nonce = str(os.urandom(16).hex())
        payload = HMACSigner.build_action_payload(action, timestamp, nonce)
        sig = HMACSigner.sign(self.agent_secret, payload)
        return {
            "X-Agent-ID": "test-agent",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sig,
        }

    def _make_message(self, **kwargs):
        defaults = {
            "from_agent": "test-agent",
            "to_agent": "remote-agent",
            "body": "Hello via relay",
        }
        defaults.update(kwargs)
        return MessageV2.create(**defaults)

    # --- Health ---

    def test_health(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

    # --- Send ---

    def test_send_valid(self):
        msg = self._make_message()
        body = msg.to_canonical_json()
        headers = self._auth_headers_body(body)
        resp = self.client.post("/api/v1/send", content=body, headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["message_id"], msg.id)
        self.assertFalse(data["duplicate"])

    def test_send_duplicate(self):
        msg = self._make_message()
        body = msg.to_canonical_json()

        headers1 = self._auth_headers_body(body)
        self.client.post("/api/v1/send", content=body, headers=headers1)

        headers2 = self._auth_headers_body(body)
        resp = self.client.post("/api/v1/send", content=body, headers=headers2)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["duplicate"])

    def test_send_invalid_signature(self):
        msg = self._make_message()
        body = msg.to_canonical_json()
        headers = self._auth_headers_body(body)
        headers["X-Signature"] = "0" * 64  # wrong signature
        resp = self.client.post("/api/v1/send", content=body, headers=headers)
        self.assertEqual(resp.status_code, 401)

    def test_send_missing_auth_headers(self):
        msg = self._make_message()
        body = msg.to_canonical_json()
        resp = self.client.post("/api/v1/send", content=body)
        self.assertEqual(resp.status_code, 401)

    def test_send_unknown_agent(self):
        msg = self._make_message()
        body = msg.to_canonical_json()
        headers = self._auth_headers_body(body)
        headers["X-Agent-ID"] = "unknown-agent"
        resp = self.client.post("/api/v1/send", content=body, headers=headers)
        self.assertEqual(resp.status_code, 401)

    # --- Poll ---

    def test_poll_valid(self):
        # Insert a message first
        msg = self._make_message(to_agent="test-agent")
        body = msg.to_canonical_json()
        send_headers = self._auth_headers_body(body)
        self.client.post("/api/v1/send", content=body, headers=send_headers)

        # Poll for it
        headers = self._auth_headers_action("POLL:test-agent")
        resp = self.client.get(
            "/api/v1/poll/test-agent?limit=10&lease_seconds=60",
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["body"], "Hello via relay")

    def test_poll_empty(self):
        headers = self._auth_headers_action("POLL:test-agent")
        resp = self.client.get("/api/v1/poll/test-agent", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["messages"]), 0)

    # --- Ack ---

    def test_ack_valid(self):
        # Send a message, poll it, then ack
        msg = self._make_message(to_agent="test-agent")
        send_body = msg.to_canonical_json()
        self.client.post("/api/v1/send", content=send_body, headers=self._auth_headers_body(send_body))

        poll_headers = self._auth_headers_action("POLL:test-agent")
        poll_resp = self.client.get("/api/v1/poll/test-agent", headers=poll_headers)
        msg_id = poll_resp.json()["messages"][0]["id"]

        ack_body = json.dumps({"agent_id": "test-agent", "message_id": msg_id},
                              sort_keys=True, separators=(",", ":"))
        ack_headers = self._auth_headers_body(ack_body)
        resp = self.client.post("/api/v1/ack", content=ack_body, headers=ack_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    # --- Nack ---

    def test_nack_valid(self):
        msg = self._make_message(to_agent="test-agent")
        send_body = msg.to_canonical_json()
        self.client.post("/api/v1/send", content=send_body, headers=self._auth_headers_body(send_body))

        poll_headers = self._auth_headers_action("POLL:test-agent")
        poll_resp = self.client.get("/api/v1/poll/test-agent", headers=poll_headers)
        msg_id = poll_resp.json()["messages"][0]["id"]

        nack_body = json.dumps(
            {"agent_id": "test-agent", "error": "test error", "message_id": msg_id},
            sort_keys=True, separators=(",", ":"),
        )
        nack_headers = self._auth_headers_body(nack_body)
        resp = self.client.post("/api/v1/nack", content=nack_body, headers=nack_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    # --- Status ---

    def test_status_valid(self):
        msg = self._make_message(to_agent="test-agent")
        send_body = msg.to_canonical_json()
        self.client.post("/api/v1/send", content=send_body, headers=self._auth_headers_body(send_body))

        headers = self._auth_headers_action(f"STATUS:{msg.id}")
        resp = self.client.get(f"/api/v1/status/{msg.id}", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("status", data)

    def test_status_not_found(self):
        headers = self._auth_headers_action("STATUS:nonexistent")
        resp = self.client.get("/api/v1/status/nonexistent", headers=headers)
        self.assertEqual(resp.status_code, 404)

    # --- Register ---

    def test_register_agent(self):
        # Body includes agent_id, but endpoint should use authenticated identity from headers
        body = json.dumps(
            {"agent_id": "new-remote", "capabilities": ["research"], "device": "pc2"},
            sort_keys=True, separators=(",", ":"),
        )
        headers = self._auth_headers_body(body)
        resp = self.client.post("/api/v1/agents/register", content=body, headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        # Security: Uses authenticated identity from X-Agent-ID header, not body
        self.assertEqual(data["agent_id"], "test-agent")

    # --- Replay protection ---

    def test_replay_rejected(self):
        """Using the same nonce twice should be rejected."""
        msg = self._make_message()
        body = msg.to_canonical_json()
        timestamp = str(time.time())
        nonce = "fixed-nonce-for-replay-test"
        payload = HMACSigner.build_body_payload(body, timestamp, nonce)
        sig = HMACSigner.sign(self.agent_secret, payload)
        headers = {
            "X-Agent-ID": "test-agent",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": sig,
            "Content-Type": "application/json",
        }

        # First request succeeds
        resp1 = self.client.post("/api/v1/send", content=body, headers=headers)
        self.assertEqual(resp1.status_code, 200)

        # Second request with same nonce is rejected
        resp2 = self.client.post("/api/v1/send", content=body, headers=headers)
        self.assertEqual(resp2.status_code, 403)


if __name__ == "__main__":
    unittest.main()
