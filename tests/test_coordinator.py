"""Unit tests for the coordinator."""

import tempfile
from pathlib import Path
from unittest import TestCase, main

from agent_comm.config import AgentCommConfig
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import PayloadType, Priority


class TestCoordinator(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coord_test_"))
        self.config = AgentCommConfig(
            project_root=self.tmp,
            db_path=self.tmp / "test.db",
        )
        self.coord = Coordinator(self.config)

    def tearDown(self):
        self.coord.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_discover(self):
        self.coord.register_agent("a1", capabilities=["code"], device="pc1")
        self.coord.register_agent("a2", capabilities=["research"], device="pc1")

        all_agents = self.coord.discover()
        self.assertEqual(len(all_agents), 2)

        code_agents = self.coord.discover(capability="code")
        self.assertEqual(len(code_agents), 1)
        self.assertEqual(code_agents[0].agent_id, "a1")

    def test_send_and_poll(self):
        msg_id = self.coord.send(
            from_agent="a1",
            to_agent="a2",
            body="Hello",
            subject="Test",
        )
        self.assertTrue(msg_id.startswith("msg_"))

        messages = self.coord.poll("a2")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body, "Hello")
        self.assertEqual(messages[0].from_agent, "a1")

    def test_send_with_payload(self):
        self.coord.send(
            from_agent="a1",
            to_agent="a2",
            body="Do this",
            payload_type=PayloadType.TASK_ASSIGNMENT,
            payload_data={"task": "research", "scope": "broad"},
        )
        messages = self.coord.poll("a2")
        self.assertEqual(messages[0].payload.type, PayloadType.TASK_ASSIGNMENT)
        self.assertEqual(messages[0].payload.data["task"], "research")

    def test_conversation_threading(self):
        conv_id = "conv_test"
        msg1_id = self.coord.send(
            from_agent="a1", to_agent="a2", body="Question",
            conversation_id=conv_id,
        )
        msg2_id = self.coord.send(
            from_agent="a2", to_agent="a1", body="Answer",
            conversation_id=conv_id, reply_to=msg1_id,
        )

        msgs = self.coord.poll("a2")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].routing.conversation_id, conv_id)

    def test_ack_and_status(self):
        msg_id = self.coord.send(from_agent="a1", to_agent="a2", body="test")
        self.coord.poll("a2")

        result = self.coord.ack(msg_id, "a2")
        self.assertTrue(result)

        status = self.coord.status(msg_id)
        self.assertIsNotNone(status)
        self.assertEqual(status["deliveries"][0]["status"], "acked")

    def test_nack(self):
        msg_id = self.coord.send(from_agent="a1", to_agent="a2", body="test")
        self.coord.poll("a2")

        result = self.coord.nack(msg_id, "a2", error="oops")
        self.assertTrue(result)

        # Message should be requeued
        messages = self.coord.poll("a2")
        self.assertEqual(len(messages), 1)

    def test_priority(self):
        self.coord.send(from_agent="a1", to_agent="a2", body="normal")
        self.coord.send(from_agent="a1", to_agent="a2", body="urgent", priority=Priority.URGENT)

        messages = self.coord.poll("a2")
        self.assertEqual(messages[0].body, "urgent")

    def test_minna_registration_calls(self):
        self.coord.register_agent("a1", capabilities=["code", "bash"])
        calls = self.coord.get_minna_registration_calls("a1")
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0]["tool"], "memory_add_entity")

    def test_minna_calls_for_unknown_agent(self):
        calls = self.coord.get_minna_registration_calls("nonexistent")
        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    main()
