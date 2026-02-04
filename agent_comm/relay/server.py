"""FastAPI relay server for cross-device agent communication.

The relay receives messages via HTTP (authenticated with HMAC-SHA256),
stores them in its own SQLite spool, and allows remote agents to poll,
ack, and nack messages.
"""

import json
import logging
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_comm import __version__
from agent_comm.message_types import MessageV2
from agent_comm.registry import LocalRegistry
from agent_comm.relay.auth import HMACSigner, NonceTracker, SecretStore
from agent_comm.relay.config import RelayConfig
from agent_comm.spool import MessageSpool
from agent_comm.transports.sqlite_transport import SQLiteTransport

logger = logging.getLogger(__name__)


def create_app(config: Optional[RelayConfig] = None) -> FastAPI:
    """Create the FastAPI relay application."""
    config = config or RelayConfig()

    app = FastAPI(title="agent-comm relay", version=__version__)

    # Server-side components (check_same_thread=False for async/threaded server)
    # Note: Both MessageSpool and SQLiteTransport open the same DB file. This is
    # intentional and safe under WAL mode — SQLite handles concurrent readers/writers.
    # MessageSpool provides insert/management; SQLiteTransport provides poll/ack/nack.
    spool = MessageSpool(config.db_path, check_same_thread=False)
    transport = SQLiteTransport(config.db_path, check_same_thread=False)
    secrets = SecretStore(config.secrets_path)
    nonces = NonceTracker(config.replay_window_seconds)
    registry = LocalRegistry(config.registry_path)

    # Store on app state for access in tests
    app.state.spool = spool
    app.state.transport = transport
    app.state.secrets = secrets
    app.state.nonces = nonces
    app.state.registry = registry
    app.state.config = config

    # --- Auth dependency ---

    async def _verify_body_auth(request: Request) -> str:
        """Verify HMAC auth for requests with a body. Returns agent_id."""
        agent_id = request.headers.get("X-Agent-ID")
        timestamp = request.headers.get("X-Timestamp")
        nonce = request.headers.get("X-Nonce")
        signature = request.headers.get("X-Signature")

        if not all([agent_id, timestamp, nonce, signature]):
            raise HTTPException(status_code=401, detail="Missing auth headers")

        secret = secrets.get(agent_id)
        if secret is None:
            raise HTTPException(status_code=401, detail="Unknown agent")

        body = (await request.body()).decode("utf-8")
        payload = HMACSigner.build_body_payload(body, timestamp, nonce)

        if not HMACSigner.verify(secret, payload, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            ts = float(timestamp)
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid timestamp")

        if not nonces.check_and_record(nonce, ts):
            raise HTTPException(status_code=403, detail="Replay detected")

        return agent_id

    async def _verify_action_auth(
        request: Request,
        action: str,
    ) -> str:
        """Verify HMAC auth for action-based requests. Returns agent_id."""
        agent_id = request.headers.get("X-Agent-ID")
        timestamp = request.headers.get("X-Timestamp")
        nonce = request.headers.get("X-Nonce")
        signature = request.headers.get("X-Signature")

        if not all([agent_id, timestamp, nonce, signature]):
            raise HTTPException(status_code=401, detail="Missing auth headers")

        secret = secrets.get(agent_id)
        if secret is None:
            raise HTTPException(status_code=401, detail="Unknown agent")

        payload = HMACSigner.build_action_payload(action, timestamp, nonce)

        if not HMACSigner.verify(secret, payload, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            ts = float(timestamp)
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid timestamp")

        if not nonces.check_and_record(nonce, ts):
            raise HTTPException(status_code=403, detail="Replay detected")

        return agent_id

    # --- Endpoints ---

    @app.get("/api/v1/health")
    async def health():
        """Health check — no authentication required."""
        return {"ok": True, "nonce_count": nonces.size}

    @app.post("/api/v1/send")
    async def send_message(request: Request):
        """Receive a message and store it in the relay spool."""
        agent_id = await _verify_body_auth(request)

        body = (await request.body()).decode("utf-8")
        try:
            message = MessageV2.from_json(body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid message: {e}")

        inserted = spool.insert(message)
        return {
            "ok": True,
            "message_id": message.id,
            "duplicate": not inserted,
        }

    @app.get("/api/v1/poll/{agent_id}")
    async def poll_messages(
        request: Request,
        agent_id: str,
        limit: int = 10,
        lease_seconds: int = 60,
    ):
        """Poll for messages addressed to an agent."""
        action = f"POLL:{agent_id}"
        await _verify_action_auth(request, action)

        messages = transport.poll(agent_id, limit=limit, lease_seconds=lease_seconds)
        return {
            "ok": True,
            "messages": [msg.model_dump(mode="json", by_alias=True) for msg in messages],
        }

    @app.post("/api/v1/ack")
    async def ack_message(request: Request):
        """Acknowledge a message."""
        # Security: Use authenticated identity from headers, not body
        auth_agent_id = await _verify_body_auth(request)

        body = (await request.body()).decode("utf-8")
        try:
            data = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        message_id = data.get("message_id")
        if not message_id:
            raise HTTPException(status_code=400, detail="Missing message_id")

        # Use authenticated agent_id, ignore any agent_id in body
        result = transport.ack(message_id, auth_agent_id)
        return {"ok": result}

    @app.post("/api/v1/nack")
    async def nack_message(request: Request):
        """Negative-acknowledge a message."""
        # Security: Use authenticated identity from headers, not body
        auth_agent_id = await _verify_body_auth(request)

        body = (await request.body()).decode("utf-8")
        try:
            data = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        message_id = data.get("message_id")
        error = data.get("error")
        if not message_id:
            raise HTTPException(status_code=400, detail="Missing message_id")

        # Use authenticated agent_id, ignore any agent_id in body
        result = transport.nack(message_id, auth_agent_id, error=error)
        return {"ok": result}

    @app.get("/api/v1/status/{message_id}")
    async def get_status(request: Request, message_id: str):
        """Get delivery status for a message."""
        action = f"STATUS:{message_id}"
        await _verify_action_auth(request, action)

        status = transport.get_status(message_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Message not found")
        return {"ok": True, "status": status}

    @app.post("/api/v1/agents/register")
    async def register_agent(request: Request):
        """Register a remote agent on the relay."""
        # Security: Use authenticated identity from headers, not body
        auth_agent_id = await _verify_body_auth(request)

        body = (await request.body()).decode("utf-8")
        try:
            data = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        from agent_comm.registry import AgentProfile

        # Always use authenticated agent_id, ignore any agent_id in body
        profile = AgentProfile(
            agent_id=auth_agent_id,
            capabilities=data.get("capabilities", []),
            transport="http",
            device=data.get("device", "remote"),
        )
        registry.register(profile)
        return {"ok": True, "agent_id": profile.agent_id}

    return app
