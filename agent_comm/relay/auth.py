"""HMAC authentication, secret management, and replay protection.

All classes use Python stdlib only (hmac, hashlib, secrets, json, time).
"""

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Optional


class SecretStore:
    """Load/save per-agent HMAC shared secrets from a JSON file.

    File format: {"agent_id": "hex-encoded-secret", ...}
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._secrets: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            self._secrets = json.loads(self.path.read_text())

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._secrets, indent=2))

    def get(self, agent_id: str) -> Optional[bytes]:
        """Get the shared secret for an agent. Returns None if not found."""
        hex_secret = self._secrets.get(agent_id)
        if hex_secret is None:
            return None
        return bytes.fromhex(hex_secret)

    def set(self, agent_id: str, secret: bytes) -> None:
        """Store a shared secret for an agent."""
        self._secrets[agent_id] = secret.hex()
        self._save()

    def generate(self, agent_id: str) -> str:
        """Generate a random 32-byte secret for an agent. Returns hex string."""
        secret = os.urandom(32)
        self.set(agent_id, secret)
        return secret.hex()

    def has(self, agent_id: str) -> bool:
        """Check if a secret exists for an agent."""
        return agent_id in self._secrets

    def list_agents(self) -> list[str]:
        """List all agent IDs with stored secrets."""
        return list(self._secrets.keys())


class HMACSigner:
    """HMAC-SHA256 signing and verification for relay requests."""

    @staticmethod
    def sign(secret: bytes, payload: str) -> str:
        """Sign a payload with HMAC-SHA256. Returns hex digest."""
        return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def verify(secret: bytes, payload: str, signature: str) -> bool:
        """Verify a payload signature. Uses constant-time comparison."""
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def build_body_payload(body: str, timestamp: str, nonce: str) -> str:
        """Build signing payload for requests with a body (POST /send, /ack, /nack)."""
        return f"{body}:{timestamp}:{nonce}"

    @staticmethod
    def build_action_payload(action: str, timestamp: str, nonce: str) -> str:
        """Build signing payload for requests without a body (GET /poll, /status).

        action is typically "POLL:{agent_id}" or "STATUS:{message_id}".
        """
        return f"{action}:{timestamp}:{nonce}"


class NonceTracker:
    """In-memory replay protection with a configurable time window.

    Rejects requests with:
    - A previously seen nonce (replay)
    - A timestamp outside the allowed window (too old or too far in future)
    """

    def __init__(self, window_seconds: int = 300):
        self._seen: dict[str, float] = {}
        self._window = window_seconds

    def check_and_record(self, nonce: str, timestamp: float) -> bool:
        """Check if a nonce is fresh. Returns True if accepted, False if rejected."""
        now = time.time()

        # Reject if timestamp is outside the window
        if abs(now - timestamp) > self._window:
            return False

        # Reject if nonce was already seen
        if nonce in self._seen:
            return False

        # Accept and record
        self._seen[nonce] = now
        self._cleanup()
        return True

    def _cleanup(self):
        """Remove expired entries from the tracker."""
        cutoff = time.time() - self._window
        self._seen = {n: t for n, t in self._seen.items() if t > cutoff}

    @property
    def size(self) -> int:
        """Number of tracked nonces (for diagnostics)."""
        return len(self._seen)
