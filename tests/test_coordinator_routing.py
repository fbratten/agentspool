"""Tests for Coordinator hybrid routing (local vs HTTP transport)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_comm.config import AgentCommConfig
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import MessageV2


class TestCoordinatorRouting(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = AgentCommConfig(
            project_root=Path(self.tmpdir),
            default_transport="sqlite",
        )

    def test_send_local_agent_uses_sqlite(self):
        """Sending to a local (sqlite) agent should use the default transport."""
        coord = Coordinator(self.config)
        coord.register_agent("local-agent", capabilities=["test"], transport="sqlite")

        msg_id = coord.send("sender", "local-agent", "hello local")
        self.assertTrue(msg_id.startswith("msg_"))

        # Verify it's in the local spool
        messages = coord.poll("local-agent")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body, "hello local")
        coord.close()

    def test_send_unregistered_agent_uses_default(self):
        """Sending to an agent not in registry falls back to default transport."""
        coord = Coordinator(self.config)
        msg_id = coord.send("sender", "unknown-agent", "hello unknown")
        self.assertTrue(msg_id.startswith("msg_"))
        coord.close()

    def test_send_http_agent_without_relay_url_uses_default(self):
        """HTTP agent without relay_url in metadata falls back to local."""
        coord = Coordinator(self.config)
        coord.register_agent("http-agent", transport="http")

        # No relay_url in metadata, so should fall back to local
        msg_id = coord.send("sender", "http-agent", "hello http")
        self.assertTrue(msg_id.startswith("msg_"))

        messages = coord.poll("http-agent")
        self.assertEqual(len(messages), 1)
        coord.close()

    @patch("agent_comm.coordinator.Coordinator._get_or_create_http_transport")
    def test_send_http_agent_with_relay_url_uses_http(self, mock_get_http):
        """HTTP agent with relay_url should use HTTPTransport."""
        mock_transport = MagicMock()
        mock_transport.send.return_value = "msg_http_123"
        mock_get_http.return_value = mock_transport

        coord = Coordinator(self.config)
        coord.register_agent(
            "remote-agent",
            transport="http",
            metadata={"relay_url": "http://relay.test:8420"},
        )

        msg_id = coord.send("sender", "remote-agent", "hello remote")
        self.assertEqual(msg_id, "msg_http_123")
        # Now passes from_agent as second argument for per-sender HMAC signing
        mock_get_http.assert_called_once_with("http://relay.test:8420", "sender")
        coord.close()

    def test_poll_always_uses_local_transport(self):
        """poll() should always use the local transport regardless of agent type."""
        coord = Coordinator(self.config)
        coord.register_agent(
            "remote-agent",
            transport="http",
            metadata={"relay_url": "http://relay.test:8420"},
        )

        # Poll should use local transport (no HTTP call)
        messages = coord.poll("remote-agent")
        self.assertEqual(len(messages), 0)
        coord.close()

    def test_ack_always_uses_local_transport(self):
        """ack() should always use the local transport."""
        coord = Coordinator(self.config)
        # Acking a non-existent message just returns False
        result = coord.ack("nonexistent", "agent-a")
        self.assertFalse(result)
        coord.close()

    def test_close_cleans_up_http_transports(self):
        """close() should close all cached HTTP transports."""
        coord = Coordinator(self.config)
        mock_transport = MagicMock()
        # Cache key is now (relay_url, sender_id) tuple
        coord._http_transports[("http://relay.test:8420", "agent-a")] = mock_transport

        coord.close()
        mock_transport.close.assert_called_once()
        self.assertEqual(len(coord._http_transports), 0)

    def test_register_with_metadata(self):
        """register_agent should accept and store metadata."""
        coord = Coordinator(self.config)
        profile = coord.register_agent(
            "remote-agent",
            capabilities=["research"],
            transport="http",
            device="pc2",
            metadata={"relay_url": "http://100.64.0.2:8420"},
        )
        self.assertEqual(profile.transport, "http")
        self.assertEqual(profile.metadata["relay_url"], "http://100.64.0.2:8420")

        # Verify persistence
        retrieved = coord.registry.get("remote-agent")
        self.assertEqual(retrieved.metadata["relay_url"], "http://100.64.0.2:8420")
        coord.close()

    def test_http_transport_cached_by_url_and_sender(self):
        """HTTPTransport instances should be cached by (relay_url, sender_id) tuple."""
        coord = Coordinator(self.config)
        mock_transport = MagicMock()
        # Cache key is now (relay_url, sender_id) for per-sender HMAC signing
        coord._http_transports[("http://relay.test:8420", "agent-a")] = mock_transport

        result = coord._get_or_create_http_transport("http://relay.test:8420", "agent-a")
        self.assertIs(result, mock_transport)
        coord.close()


if __name__ == "__main__":
    unittest.main()
