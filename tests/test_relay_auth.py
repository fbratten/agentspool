"""Tests for relay authentication module (auth.py)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from agent_comm.relay.auth import HMACSigner, NonceTracker, SecretStore


class TestSecretStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = Path(self.tmpdir) / "secrets.json"

    def test_generate_creates_secret(self):
        store = SecretStore(self.path)
        hex_secret = store.generate("agent-a")
        self.assertEqual(len(hex_secret), 64)  # 32 bytes = 64 hex chars
        self.assertTrue(store.has("agent-a"))

    def test_get_returns_bytes(self):
        store = SecretStore(self.path)
        store.generate("agent-a")
        secret = store.get("agent-a")
        self.assertIsInstance(secret, bytes)
        self.assertEqual(len(secret), 32)

    def test_get_nonexistent_returns_none(self):
        store = SecretStore(self.path)
        self.assertIsNone(store.get("nonexistent"))

    def test_set_and_get(self):
        store = SecretStore(self.path)
        secret = os.urandom(32)
        store.set("agent-b", secret)
        retrieved = store.get("agent-b")
        self.assertEqual(secret, retrieved)

    def test_persistence_across_instances(self):
        store1 = SecretStore(self.path)
        store1.generate("agent-a")
        secret1 = store1.get("agent-a")

        store2 = SecretStore(self.path)
        secret2 = store2.get("agent-a")
        self.assertEqual(secret1, secret2)

    def test_list_agents(self):
        store = SecretStore(self.path)
        store.generate("agent-a")
        store.generate("agent-b")
        agents = store.list_agents()
        self.assertEqual(sorted(agents), ["agent-a", "agent-b"])

    def test_file_created_on_save(self):
        self.assertFalse(self.path.exists())
        store = SecretStore(self.path)
        store.generate("agent-a")
        self.assertTrue(self.path.exists())

    def test_has_returns_false_for_missing(self):
        store = SecretStore(self.path)
        self.assertFalse(store.has("nonexistent"))


class TestHMACSigner(unittest.TestCase):
    def setUp(self):
        self.secret = os.urandom(32)

    def test_sign_returns_hex(self):
        sig = HMACSigner.sign(self.secret, "test payload")
        self.assertEqual(len(sig), 64)  # SHA256 = 64 hex chars

    def test_verify_correct(self):
        payload = "test payload"
        sig = HMACSigner.sign(self.secret, payload)
        self.assertTrue(HMACSigner.verify(self.secret, payload, sig))

    def test_verify_wrong_secret(self):
        payload = "test payload"
        sig = HMACSigner.sign(self.secret, payload)
        wrong_secret = os.urandom(32)
        self.assertFalse(HMACSigner.verify(wrong_secret, payload, sig))

    def test_verify_wrong_payload(self):
        sig = HMACSigner.sign(self.secret, "original")
        self.assertFalse(HMACSigner.verify(self.secret, "tampered", sig))

    def test_build_body_payload(self):
        result = HMACSigner.build_body_payload("body", "1234", "nonce1")
        self.assertEqual(result, "body:1234:nonce1")

    def test_build_action_payload(self):
        result = HMACSigner.build_action_payload("POLL:agent-a", "1234", "nonce1")
        self.assertEqual(result, "POLL:agent-a:1234:nonce1")

    def test_sign_canonical_json(self):
        """Verify signing works with actual canonical JSON."""
        data = {"from": "a", "to": "b", "body": "hello"}
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        sig = HMACSigner.sign(self.secret, HMACSigner.build_body_payload(canonical, "123", "n1"))
        self.assertTrue(len(sig) == 64)


class TestNonceTracker(unittest.TestCase):
    def test_fresh_nonce_accepted(self):
        tracker = NonceTracker(window_seconds=300)
        self.assertTrue(tracker.check_and_record("nonce1", time.time()))

    def test_replayed_nonce_rejected(self):
        tracker = NonceTracker(window_seconds=300)
        ts = time.time()
        self.assertTrue(tracker.check_and_record("nonce1", ts))
        self.assertFalse(tracker.check_and_record("nonce1", ts))

    def test_expired_timestamp_rejected(self):
        tracker = NonceTracker(window_seconds=300)
        old_ts = time.time() - 600  # 10 minutes ago
        self.assertFalse(tracker.check_and_record("nonce1", old_ts))

    def test_future_timestamp_rejected(self):
        tracker = NonceTracker(window_seconds=300)
        future_ts = time.time() + 600  # 10 minutes from now
        self.assertFalse(tracker.check_and_record("nonce1", future_ts))

    def test_different_nonces_accepted(self):
        tracker = NonceTracker(window_seconds=300)
        ts = time.time()
        self.assertTrue(tracker.check_and_record("nonce1", ts))
        self.assertTrue(tracker.check_and_record("nonce2", ts))

    def test_size_property(self):
        tracker = NonceTracker(window_seconds=300)
        ts = time.time()
        tracker.check_and_record("a", ts)
        tracker.check_and_record("b", ts)
        self.assertEqual(tracker.size, 2)

    def test_cleanup_removes_old_entries(self):
        tracker = NonceTracker(window_seconds=1)
        tracker.check_and_record("old", time.time())
        time.sleep(1.5)
        # New record triggers cleanup
        tracker.check_and_record("new", time.time())
        self.assertEqual(tracker.size, 1)


if __name__ == "__main__":
    unittest.main()
