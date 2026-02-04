"""Unit tests for SQLite message spool."""

import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main

from agent_comm.message_types import MessageV2, PayloadType, Priority
from agent_comm.spool import MessageSpool


class TestMessageSpool(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="spool_test_"))
        self.db_path = self.tmp / "test.db"
        self.spool = MessageSpool(self.db_path)

    def tearDown(self):
        self.spool.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_msg(self, from_a="agent-a", to_a="agent-b", body="test", **kwargs):
        return MessageV2.create(from_agent=from_a, to_agent=to_a, body=body, **kwargs)

    # --- Insert tests ---

    def test_insert_creates_message_and_delivery(self):
        msg = self._make_msg()
        result = self.spool.insert(msg)
        self.assertTrue(result)

        conn = self.spool._get_conn()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg.id,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["from_agent"], "agent-a")
        self.assertEqual(row["to_agent"], "agent-b")

        delivery = conn.execute("SELECT * FROM deliveries WHERE message_id = ?", (msg.id,)).fetchone()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["status"], "queued")
        self.assertEqual(delivery["recipient_agent_id"], "agent-b")

    def test_insert_idempotent(self):
        msg = self._make_msg()
        self.assertTrue(self.spool.insert(msg))
        self.assertFalse(self.spool.insert(msg))

    def test_insert_sets_server_time_ttl(self):
        msg = self._make_msg(ttl_hours=2)
        self.spool.insert(msg)

        conn = self.spool._get_conn()
        row = conn.execute("SELECT expires_at FROM messages WHERE id = ?", (msg.id,)).fetchone()
        expires = datetime.fromisoformat(row["expires_at"])
        now = datetime.now(timezone.utc)
        # expires should be ~2 hours from now
        diff = (expires - now).total_seconds()
        self.assertGreater(diff, 7000)  # > ~1h57m
        self.assertLess(diff, 7300)     # < ~2h2m

    def test_insert_with_payload(self):
        msg = self._make_msg(
            payload_type=PayloadType.TASK_ASSIGNMENT,
            payload_data={"task_id": "t1", "scope": "broad"},
        )
        self.spool.insert(msg)

        conn = self.spool._get_conn()
        row = conn.execute("SELECT payload_type, payload_data FROM messages WHERE id = ?", (msg.id,)).fetchone()
        self.assertEqual(row["payload_type"], "task_assignment")
        self.assertIn("task_id", row["payload_data"])

    # --- Poll tests ---

    def test_poll_returns_queued_messages(self):
        msg = self._make_msg()
        self.spool.insert(msg)

        results = self.spool.poll("agent-b", limit=10, lease_seconds=60)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], msg.id)

    def test_poll_leases_message(self):
        msg = self._make_msg()
        self.spool.insert(msg)
        self.spool.poll("agent-b", limit=10, lease_seconds=60)

        conn = self.spool._get_conn()
        delivery = conn.execute(
            "SELECT status, attempt FROM deliveries WHERE message_id = ?", (msg.id,)
        ).fetchone()
        self.assertEqual(delivery["status"], "leased")
        self.assertEqual(delivery["attempt"], 1)

    def test_poll_skips_leased_messages(self):
        msg = self._make_msg()
        self.spool.insert(msg)
        self.spool.poll("agent-b", limit=10, lease_seconds=300)

        # Second poll should return nothing (message is leased)
        results = self.spool.poll("agent-b", limit=10, lease_seconds=300)
        self.assertEqual(len(results), 0)

    def test_poll_reclaims_expired_lease(self):
        msg = self._make_msg()
        self.spool.insert(msg)
        self.spool.poll("agent-b", limit=10, lease_seconds=1)

        # Wait for lease to expire
        time.sleep(1.5)

        results = self.spool.poll("agent-b", limit=10, lease_seconds=60)
        self.assertEqual(len(results), 1)

    def test_poll_respects_limit(self):
        for i in range(5):
            self.spool.insert(self._make_msg(body=f"msg-{i}"))

        results = self.spool.poll("agent-b", limit=2, lease_seconds=60)
        self.assertEqual(len(results), 2)

    def test_poll_skips_expired_messages(self):
        msg = self._make_msg(ttl_hours=0)  # expires immediately
        self.spool.insert(msg, ttl_hours=0)

        # Manually set expires_at to past
        conn = self.spool._get_conn()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn.execute("UPDATE messages SET expires_at = ? WHERE id = ?", (past, msg.id))

        results = self.spool.poll("agent-b", limit=10, lease_seconds=60)
        self.assertEqual(len(results), 0)

    def test_poll_wrong_agent_gets_nothing(self):
        self.spool.insert(self._make_msg(to_a="agent-b"))
        results = self.spool.poll("agent-c", limit=10)
        self.assertEqual(len(results), 0)

    def test_poll_priority_ordering(self):
        msg_low = self._make_msg(body="low", priority=Priority.LOW)
        msg_urgent = self._make_msg(body="urgent", priority=Priority.URGENT)
        msg_normal = self._make_msg(body="normal")
        self.spool.insert(msg_low)
        self.spool.insert(msg_urgent)
        self.spool.insert(msg_normal)

        results = self.spool.poll("agent-b", limit=10, lease_seconds=60)
        # Urgent should come first
        self.assertEqual(results[0]["body"], "urgent")

    # --- Ack tests ---

    def test_ack_marks_delivered(self):
        msg = self._make_msg()
        self.spool.insert(msg)
        self.spool.poll("agent-b", limit=10, lease_seconds=60)

        result = self.spool.ack(msg.id, "agent-b")
        self.assertTrue(result)

        conn = self.spool._get_conn()
        delivery = conn.execute(
            "SELECT status FROM deliveries WHERE message_id = ?", (msg.id,)
        ).fetchone()
        self.assertEqual(delivery["status"], "acked")

    def test_ack_only_works_on_leased(self):
        msg = self._make_msg()
        self.spool.insert(msg)
        # Don't poll (still queued)
        result = self.spool.ack(msg.id, "agent-b")
        self.assertFalse(result)

    # --- Nack tests ---

    def test_nack_requeues(self):
        msg = self._make_msg()
        self.spool.insert(msg)
        self.spool.poll("agent-b", limit=10, lease_seconds=60)

        result = self.spool.nack(msg.id, "agent-b", error="processing failed")
        self.assertTrue(result)

        conn = self.spool._get_conn()
        delivery = conn.execute(
            "SELECT status, last_error FROM deliveries WHERE message_id = ?", (msg.id,)
        ).fetchone()
        self.assertEqual(delivery["status"], "queued")
        self.assertEqual(delivery["last_error"], "processing failed")

    def test_nack_marks_failed_after_max_attempts(self):
        msg = self._make_msg()
        self.spool.insert(msg)

        # Poll, nack, wait for lease, repeat — exhaust all 3 attempts
        for i in range(3):
            results = self.spool.poll("agent-b", limit=10, lease_seconds=1)
            if results:
                self.spool.nack(msg.id, "agent-b", error=f"attempt {i+1}")
            time.sleep(1.2)  # wait for lease to expire

        conn = self.spool._get_conn()
        delivery = conn.execute(
            "SELECT status, attempt FROM deliveries WHERE message_id = ?", (msg.id,)
        ).fetchone()
        self.assertEqual(delivery["status"], "failed")
        self.assertEqual(delivery["attempt"], 3)

    # --- Conversation tests ---

    def test_conversation_threading(self):
        conv_id = "conv_test"
        msg1 = self._make_msg(body="first", conversation_id=conv_id)
        msg2 = self._make_msg(body="second", conversation_id=conv_id, reply_to=msg1.id)
        self.spool.insert(msg1)
        self.spool.insert(msg2)

        thread = self.spool.get_conversation(conv_id)
        self.assertEqual(len(thread), 2)
        self.assertEqual(thread[0]["body"], "first")
        self.assertEqual(thread[1]["body"], "second")
        self.assertEqual(thread[1]["reply_to"], msg1.id)

    # --- Cleanup tests ---

    def test_cleanup_removes_expired(self):
        msg = self._make_msg()
        self.spool.insert(msg)

        # Set expires_at to past
        conn = self.spool._get_conn()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn.execute("UPDATE messages SET expires_at = ? WHERE id = ?", (past, msg.id))

        count = self.spool.cleanup_expired()
        self.assertEqual(count, 1)

        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg.id,)).fetchone()
        self.assertIsNone(row)

    def test_cleanup_preserves_valid(self):
        msg = self._make_msg()
        self.spool.insert(msg)

        count = self.spool.cleanup_expired()
        self.assertEqual(count, 0)

    # --- Stats tests ---

    def test_stats(self):
        self.spool.insert(self._make_msg(body="one"))
        self.spool.insert(self._make_msg(body="two"))
        stats = self.spool.stats()
        self.assertEqual(stats["total_messages"], 2)
        self.assertIn("queued", stats["deliveries"])
        self.assertEqual(stats["deliveries"]["queued"], 2)


if __name__ == "__main__":
    main()
