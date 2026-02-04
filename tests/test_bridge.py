"""Unit tests for the bridge system (OpenClaw bridge + runner)."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import MagicMock, patch

from agent_comm.bridges import BridgeResult
from agent_comm.bridges.openclaw_bridge import OpenClawBridge, OpenClawConfig
from agent_comm.bridges.openclaw_direct_bridge import OpenClawDirectBridge, DirectBridgeConfig
from agent_comm.bridges.runner import BridgeRunner
from agent_comm.config import AgentCommConfig
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import MessageV2, PayloadType


class TestOpenClawBridge(TestCase):

    def setUp(self):
        self.config = OpenClawConfig(
            agent_id="gateway-agent",
            wsl_instance="GatewayAgent",
            openclaw_agent="main",
            timeout_seconds=10,
        )
        self.bridge = OpenClawBridge(self.config)

    def test_agent_id(self):
        self.assertEqual(self.bridge.agent_id, "gateway-agent")

    def test_build_message_text_body_only(self):
        msg = MessageV2.create(from_agent="a", to_agent="b", body="Hello")
        text = self.bridge._build_message_text(msg)
        self.assertEqual(text, "Hello")

    def test_build_message_text_with_subject(self):
        msg = MessageV2.create(from_agent="a", to_agent="b", body="Hello", subject="Test")
        text = self.bridge._build_message_text(msg)
        self.assertIn("[Subject: Test]", text)
        self.assertIn("Hello", text)

    def test_build_message_text_with_payload(self):
        msg = MessageV2.create(
            from_agent="a", to_agent="b", body="Do this",
            payload_type=PayloadType.TASK_ASSIGNMENT,
            payload_data={"task": "research"},
        )
        text = self.bridge._build_message_text(msg)
        self.assertIn("Do this", text)
        self.assertIn("task_assignment", text)
        self.assertIn("research", text)

    def test_build_command(self):
        cmd = self.bridge._build_command("Hello world")
        self.assertEqual(cmd[0], "wsl")
        self.assertEqual(cmd[1], "-d")
        self.assertEqual(cmd[2], "GatewayAgent")
        self.assertEqual(cmd[3], "-e")
        self.assertEqual(cmd[4], "bash")
        self.assertEqual(cmd[5], "-c")
        self.assertIn("openclaw agent", cmd[6])
        self.assertIn("--agent main", cmd[6])
        self.assertIn("--json", cmd[6])

    def test_build_command_with_path_setup(self):
        self.config.path_setup = "export PATH=/opt/bin:$PATH"
        bridge = OpenClawBridge(self.config)
        cmd = bridge._build_command("test")
        self.assertIn("export PATH=/opt/bin:$PATH", cmd[6])
        self.assertIn("openclaw agent", cmd[6])

    def test_parse_response_valid_json(self):
        response = json.dumps({
            "result": {
                "payloads": [
                    {"text": "I found 3 sources."},
                    {"text": "Here are the details."},
                ]
            }
        })
        text = self.bridge._parse_response(response)
        self.assertEqual(text, "I found 3 sources.\nHere are the details.")

    def test_parse_response_no_payloads(self):
        response = json.dumps({"result": {"payloads": []}})
        text = self.bridge._parse_response(response)
        # Falls back to JSON dump
        self.assertIn("result", text)

    def test_parse_response_not_json(self):
        text = self.bridge._parse_response("plain text output")
        self.assertEqual(text, "plain text output")

    @patch("agent_comm.bridges.openclaw_bridge.subprocess.run")
    def test_forward_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "result": {"payloads": [{"text": "Done!"}]}
            }),
            stderr="",
        )
        msg = MessageV2.create(from_agent="claude", to_agent="nelly-pc2", body="Research AI")
        result = self.bridge.forward(msg)

        self.assertTrue(result.success)
        self.assertEqual(result.reply_text, "Done!")
        mock_run.assert_called_once()

    @patch("agent_comm.bridges.openclaw_bridge.subprocess.run")
    def test_forward_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Gateway not running",
        )
        msg = MessageV2.create(from_agent="claude", to_agent="nelly-pc2", body="test")
        result = self.bridge.forward(msg)

        self.assertFalse(result.success)
        self.assertIn("Gateway not running", result.error)

    @patch("agent_comm.bridges.openclaw_bridge.subprocess.run")
    def test_forward_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="wsl", timeout=10)

        msg = MessageV2.create(from_agent="claude", to_agent="nelly-pc2", body="test")
        result = self.bridge.forward(msg)

        self.assertFalse(result.success)
        self.assertIn("Timeout", result.error)

    @patch("agent_comm.bridges.openclaw_bridge.subprocess.run")
    def test_forward_wsl_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        msg = MessageV2.create(from_agent="claude", to_agent="nelly-pc2", body="test")
        result = self.bridge.forward(msg)

        self.assertFalse(result.success)
        self.assertIn("wsl", result.error)

    @patch("agent_comm.bridges.openclaw_bridge.subprocess.run")
    def test_is_available_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        self.assertTrue(self.bridge.is_available())

    @patch("agent_comm.bridges.openclaw_bridge.subprocess.run")
    def test_is_available_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        self.assertFalse(self.bridge.is_available())


class TestOpenClawDirectBridge(TestCase):
    """Tests for OpenClawDirectBridge (in-WSL mode, no wsl wrapper)."""

    def setUp(self):
        self.config = DirectBridgeConfig(
            agent_id="nelly",
            openclaw_agent="main",
            path_setup='export PATH="/opt/bin:$PATH"',
            env_vars={"GOG_KEYRING_PASSWORD": "test-password"},
            timeout_seconds=10,
        )
        self.bridge = OpenClawDirectBridge(self.config)

    def test_agent_id(self):
        self.assertEqual(self.bridge.agent_id, "nelly")

    def test_build_message_text_body_only(self):
        msg = MessageV2.create(from_agent="a", to_agent="b", body="Hello")
        text = self.bridge._build_message_text(msg)
        self.assertEqual(text, "Hello")

    def test_build_message_text_with_subject(self):
        msg = MessageV2.create(from_agent="a", to_agent="b", body="Hello", subject="Test")
        text = self.bridge._build_message_text(msg)
        self.assertIn("[Subject: Test]", text)
        self.assertIn("Hello", text)

    def test_build_message_text_with_payload(self):
        msg = MessageV2.create(
            from_agent="a", to_agent="b", body="Do this",
            payload_type=PayloadType.TASK_ASSIGNMENT,
            payload_data={"task": "research"},
        )
        text = self.bridge._build_message_text(msg)
        self.assertIn("Do this", text)
        self.assertIn("task_assignment", text)
        self.assertIn("research", text)

    def test_build_command_direct(self):
        """Direct bridge builds a shell command string (not list)."""
        cmd = self.bridge._build_command("Hello world")
        # Should be a string with path setup and openclaw
        self.assertIsInstance(cmd, str)
        self.assertIn('export PATH="/opt/bin:$PATH"', cmd)
        self.assertIn("openclaw agent", cmd)
        self.assertIn("--agent main", cmd)
        self.assertIn("--json", cmd)

    def test_build_command_without_path_setup(self):
        config = DirectBridgeConfig(agent_id="test", path_setup=None)
        bridge = OpenClawDirectBridge(config)
        cmd = bridge._build_command("test")
        # Should just be the openclaw command
        self.assertIn("openclaw agent", cmd)
        self.assertNotIn("export", cmd)

    def test_build_env_includes_custom_vars(self):
        env = self.bridge._build_env()
        self.assertEqual(env.get("GOG_KEYRING_PASSWORD"), "test-password")

    def test_parse_response_valid_json(self):
        response = json.dumps({
            "result": {
                "payloads": [
                    {"text": "I found 3 sources."},
                    {"text": "Here are the details."},
                ]
            }
        })
        text = self.bridge._parse_response(response)
        self.assertEqual(text, "I found 3 sources.\nHere are the details.")

    def test_parse_response_no_payloads(self):
        response = json.dumps({"result": {"payloads": []}})
        text = self.bridge._parse_response(response)
        # Falls back to JSON dump
        self.assertIn("result", text)

    def test_parse_response_not_json(self):
        text = self.bridge._parse_response("plain text output")
        self.assertEqual(text, "plain text output")

    @patch("agent_comm.bridges.openclaw_direct_bridge.subprocess.run")
    def test_forward_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "result": {"payloads": [{"text": "Done!"}]}
            }),
            stderr="",
        )
        msg = MessageV2.create(from_agent="claude", to_agent="nelly", body="Research AI")
        result = self.bridge.forward(msg)

        self.assertTrue(result.success)
        self.assertEqual(result.reply_text, "Done!")
        mock_run.assert_called_once()
        # Verify shell=True is used
        call_kwargs = mock_run.call_args.kwargs
        self.assertTrue(call_kwargs.get("shell"))

    @patch("agent_comm.bridges.openclaw_direct_bridge.subprocess.run")
    def test_forward_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Gateway not running",
        )
        msg = MessageV2.create(from_agent="claude", to_agent="nelly", body="test")
        result = self.bridge.forward(msg)

        self.assertFalse(result.success)
        self.assertIn("Gateway not running", result.error)

    @patch("agent_comm.bridges.openclaw_direct_bridge.subprocess.run")
    def test_forward_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="openclaw", timeout=10)

        msg = MessageV2.create(from_agent="claude", to_agent="nelly", body="test")
        result = self.bridge.forward(msg)

        self.assertFalse(result.success)
        self.assertIn("Timeout", result.error)

    @patch("agent_comm.bridges.openclaw_direct_bridge.subprocess.run")
    def test_forward_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        msg = MessageV2.create(from_agent="claude", to_agent="nelly", body="test")
        result = self.bridge.forward(msg)

        self.assertFalse(result.success)
        self.assertIn("not found", result.error.lower())

    @patch("agent_comm.bridges.openclaw_direct_bridge.subprocess.run")
    def test_is_available_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="2026.1.29\n", stderr="")
        self.assertTrue(self.bridge.is_available())

    @patch("agent_comm.bridges.openclaw_direct_bridge.subprocess.run")
    def test_is_available_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        self.assertFalse(self.bridge.is_available())


class TestBridgeRunner(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runner_test_"))
        config = AgentCommConfig(project_root=self.tmp, db_path=self.tmp / "test.db")
        self.coord = Coordinator(config)

        self.mock_bridge = MagicMock()
        self.mock_bridge.agent_id = "nelly-pc2"
        self.runner = BridgeRunner(
            coordinator=self.coord,
            bridge=self.mock_bridge,
            poll_interval=0.1,
        )

    def tearDown(self):
        self.coord.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_once_no_messages(self):
        count = self.runner.run_once()
        self.assertEqual(count, 0)
        self.mock_bridge.forward.assert_not_called()

    def test_run_once_forwards_and_acks(self):
        # Send a message to nelly-pc2
        self.coord.send(
            from_agent="claude-code",
            to_agent="nelly-pc2",
            body="Research this topic",
            subject="Research task",
            conversation_id="conv_001",
        )

        # Mock bridge returns a reply
        self.mock_bridge.forward.return_value = BridgeResult(
            success=True,
            reply_text="Found 3 sources on the topic.",
        )

        count = self.runner.run_once()
        self.assertEqual(count, 1)
        self.mock_bridge.forward.assert_called_once()

        # Check reply was inserted into spool for claude-code
        replies = self.coord.poll("claude-code")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].from_agent, "nelly-pc2")
        self.assertEqual(replies[0].body, "Found 3 sources on the topic.")
        self.assertEqual(replies[0].routing.conversation_id, "conv_001")
        self.assertEqual(replies[0].subject, "Re: Research task")

    def test_run_once_nacks_on_failure(self):
        self.coord.send(
            from_agent="claude-code",
            to_agent="nelly-pc2",
            body="test",
        )

        self.mock_bridge.forward.return_value = BridgeResult(
            success=False,
            error="Gateway down",
        )

        count = self.runner.run_once()
        self.assertEqual(count, 0)

        # Message should be requeued (nacked), not acked
        # Poll again — it should be available after nack
        messages = self.coord.poll("nelly-pc2")
        self.assertEqual(len(messages), 1)

    def test_run_once_multiple_messages(self):
        # Send 3 messages
        for i in range(3):
            self.coord.send(
                from_agent="claude-code",
                to_agent="nelly-pc2",
                body=f"Message {i}",
            )

        self.mock_bridge.forward.return_value = BridgeResult(
            success=True, reply_text="OK",
        )

        # Runner processes max 1 per poll by default
        count = self.runner.run_once()
        self.assertEqual(count, 1)
        self.assertEqual(self.mock_bridge.forward.call_count, 1)

    def test_stop(self):
        self.runner._running = True
        self.runner.stop()
        self.assertFalse(self.runner._running)


if __name__ == "__main__":
    main()
