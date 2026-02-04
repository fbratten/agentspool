"""HTTP transport — client-side transport for cross-device communication.

Sends messages to a remote relay server via HTTP with HMAC authentication.
Implements the Transport ABC so it plugs into the Coordinator seamlessly.
"""

import json
import logging
import time
import uuid
from typing import Optional

import httpx

from agent_comm.message_types import MessageV2
from agent_comm.relay.auth import HMACSigner
from agent_comm.transports import Transport

logger = logging.getLogger(__name__)


class HTTPTransport(Transport):
    """Transport that forwards messages to a remote relay server via HTTP.

    Each request is authenticated with HMAC-SHA256 using a per-agent shared secret.
    """

    def __init__(
        self,
        relay_url: str,
        agent_id: str,
        secret: bytes,
        timeout: float = 30.0,
    ):
        self._url = relay_url.rstrip("/")
        self._agent_id = agent_id
        self._secret = secret
        self._client = httpx.Client(timeout=timeout)

    def _auth_headers(self, payload: str) -> dict[str, str]:
        """Build HMAC auth headers for a request."""
        timestamp = str(time.time())
        nonce = str(uuid.uuid4())
        signature = HMACSigner.sign(
            self._secret,
            HMACSigner.build_body_payload(payload, timestamp, nonce),
        )
        return {
            "X-Agent-ID": self._agent_id,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        }

    def _action_auth_headers(self, action: str) -> dict[str, str]:
        """Build HMAC auth headers for action-based requests (GET)."""
        timestamp = str(time.time())
        nonce = str(uuid.uuid4())
        signature = HMACSigner.sign(
            self._secret,
            HMACSigner.build_action_payload(action, timestamp, nonce),
        )
        return {
            "X-Agent-ID": self._agent_id,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        }

    def send(self, message: MessageV2) -> str:
        """Send a message to the relay server."""
        canonical = message.to_canonical_json()
        headers = self._auth_headers(canonical)
        headers["Content-Type"] = "application/json"

        resp = self._client.post(
            f"{self._url}/api/v1/send",
            content=canonical,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["message_id"]

    def poll(
        self,
        agent_id: str,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[MessageV2]:
        """Poll the relay server for messages addressed to agent_id."""
        action = f"POLL:{agent_id}"
        headers = self._action_auth_headers(action)

        resp = self._client.get(
            f"{self._url}/api/v1/poll/{agent_id}",
            params={"limit": limit, "lease_seconds": lease_seconds},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        messages = []
        for msg_json in data.get("messages", []):
            if isinstance(msg_json, str):
                messages.append(MessageV2.from_json(msg_json))
            else:
                messages.append(MessageV2.model_validate(msg_json))
        return messages

    def ack(self, message_id: str, agent_id: str) -> bool:
        """Acknowledge a message on the relay server."""
        body = json.dumps(
            {"message_id": message_id, "agent_id": agent_id},
            sort_keys=True,
            separators=(",", ":"),
        )
        headers = self._auth_headers(body)
        headers["Content-Type"] = "application/json"

        resp = self._client.post(
            f"{self._url}/api/v1/ack",
            content=body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)

    def nack(self, message_id: str, agent_id: str, error: Optional[str] = None) -> bool:
        """Nack a message on the relay server."""
        payload = {"message_id": message_id, "agent_id": agent_id}
        if error:
            payload["error"] = error
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        headers = self._auth_headers(body)
        headers["Content-Type"] = "application/json"

        resp = self._client.post(
            f"{self._url}/api/v1/nack",
            content=body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)

    def get_status(self, message_id: str) -> Optional[dict]:
        """Get delivery status from the relay server."""
        action = f"STATUS:{message_id}"
        headers = self._action_auth_headers(action)

        resp = self._client.get(
            f"{self._url}/api/v1/status/{message_id}",
            headers=headers,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("status")

    def close(self):
        """Close the HTTP client."""
        self._client.close()
