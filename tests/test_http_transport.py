"""Tests for HTTPTransport (client-side transport with mocked httpx)."""

import json
import os
import unittest
from unittest.mock import MagicMock

from agent_comm.message_types import MessageV2, Priority
from agent_comm.transports.http_transport import HTTPTransport


class TestHTTPTransport(unittest.TestCase):
    def setUp(self):
        self.secret = os.urandom(32)
        self.transport = HTTPTransport(
            relay_url="http://relay.test:8420",
            agent_id="sender-agent",
            secret=self.secret,
            timeout=10.0,
        )
        # Replace the real httpx.Client with a mock
        self.mock_client = MagicMock()
        self.transport._client = self.mock_client

    def tearDown(self):
        # Don't call close on the real client (it's already mocked)
        pass

    def _make_message(self, **kwargs):
        defaults = {
            "from_agent": "sender-agent",
            "to_agent": "receiver-agent",
            "body": "Hello remote",
        }
        defaults.update(kwargs)
        return MessageV2.create(**defaults)

    def _mock_response(self, data, status_code=200):
        mock_resp = MagicMock()
        mock_resp.json.return_value = data
        mock_resp.status_code = status_code
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_send_success(self):
        self.mock_client.post.return_value = self._mock_response(
            {"ok": True, "message_id": "msg_123", "duplicate": False}
        )

        msg = self._make_message()
        result = self.transport.send(msg)

        self.assertEqual(result, "msg_123")
        self.mock_client.post.assert_called_once()
        call_url = self.mock_client.post.call_args[0][0]
        self.assertIn("/api/v1/send", call_url)

    def test_send_includes_auth_headers(self):
        self.mock_client.post.return_value = self._mock_response(
            {"ok": True, "message_id": "msg_123"}
        )

        msg = self._make_message()
        self.transport.send(msg)

        headers = self.mock_client.post.call_args[1]["headers"]
        self.assertEqual(headers["X-Agent-ID"], "sender-agent")
        self.assertIn("X-Timestamp", headers)
        self.assertIn("X-Nonce", headers)
        self.assertIn("X-Signature", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_poll_returns_messages(self):
        msg = self._make_message()
        self.mock_client.get.return_value = self._mock_response({
            "ok": True,
            "messages": [msg.model_dump(mode="json", by_alias=True)],
        })

        messages = self.transport.poll("receiver-agent", limit=5, lease_seconds=30)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body, "Hello remote")
        call_url = self.mock_client.get.call_args[0][0]
        self.assertIn("/api/v1/poll/receiver-agent", call_url)

    def test_poll_empty(self):
        self.mock_client.get.return_value = self._mock_response(
            {"ok": True, "messages": []}
        )

        messages = self.transport.poll("receiver-agent")
        self.assertEqual(len(messages), 0)

    def test_ack_success(self):
        self.mock_client.post.return_value = self._mock_response({"ok": True})

        result = self.transport.ack("msg_123", "receiver-agent")

        self.assertTrue(result)
        call_url = self.mock_client.post.call_args[0][0]
        self.assertIn("/api/v1/ack", call_url)

    def test_nack_success(self):
        self.mock_client.post.return_value = self._mock_response({"ok": True})

        result = self.transport.nack("msg_123", "receiver-agent", error="timeout")

        self.assertTrue(result)
        body = json.loads(self.mock_client.post.call_args[1]["content"])
        self.assertEqual(body["error"], "timeout")

    def test_nack_without_error(self):
        self.mock_client.post.return_value = self._mock_response({"ok": True})

        self.transport.nack("msg_123", "receiver-agent")

        body = json.loads(self.mock_client.post.call_args[1]["content"])
        self.assertNotIn("error", body)

    def test_get_status(self):
        self.mock_client.get.return_value = self._mock_response({
            "ok": True,
            "status": {"message_id": "msg_123", "deliveries": []},
        })

        status = self.transport.get_status("msg_123")

        self.assertIsNotNone(status)
        self.assertEqual(status["message_id"], "msg_123")

    def test_get_status_not_found(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        self.mock_client.get.return_value = mock_resp

        status = self.transport.get_status("nonexistent")
        self.assertIsNone(status)

    def test_close(self):
        self.transport.close()
        self.mock_client.close.assert_called_once()

    def test_url_trailing_slash_stripped(self):
        t = HTTPTransport("http://relay.test:8420/", "a", self.secret)
        self.assertEqual(t._url, "http://relay.test:8420")
        t.close()


if __name__ == "__main__":
    unittest.main()
