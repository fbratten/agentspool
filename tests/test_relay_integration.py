"""End-to-end HTTP relay integration tests — real uvicorn server, no mocks.

Starts a relay server on a random localhost port, creates HTTPTransport
instances with real HMAC secrets, and exercises the full message lifecycle
over actual TCP/HTTP connections.
"""

import os
import socket
import time
from pathlib import Path
from threading import Thread

import httpx
import pytest
import uvicorn

from agent_comm.message_types import MessageV2, Priority
from agent_comm.relay.config import RelayConfig
from agent_comm.relay.server import create_app
from agent_comm.transports.http_transport import HTTPTransport


# ==================== Helpers ====================


def _find_free_port() -> int:
    """Get an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(base_url: str, timeout: float = 3.0):
    """Block until the relay server responds to /api/v1/health."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/v1/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Relay server at {base_url} did not start within {timeout}s")


# ==================== Fixtures ====================


@pytest.fixture(scope="module")
def relay_server(tmp_path_factory):
    """Start a real uvicorn relay server. Yields config dict."""
    tmpdir = tmp_path_factory.mktemp("relay_integration")
    port = _find_free_port()

    config = RelayConfig(
        project_root=tmpdir,
        host="127.0.0.1",
        port=port,
        secrets_path=tmpdir / "secrets.json",
        db_path=tmpdir / "relay.db",
        registry_path=tmpdir / "registry.json",
    )
    app = create_app(config)

    # Register agent secrets
    alice_secret = os.urandom(32)
    bob_secret = os.urandom(32)
    app.state.secrets.set("alice", alice_secret)
    app.state.secrets.set("bob", bob_secret)

    uv_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(uv_config)
    thread = Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    _wait_for_server(base_url)

    yield {
        "base_url": base_url,
        "app": app,
        "tmpdir": tmpdir,
        "alice_secret": alice_secret,
        "bob_secret": bob_secret,
    }

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def alice_transport(relay_server):
    """HTTPTransport for agent 'alice'."""
    t = HTTPTransport(
        relay_url=relay_server["base_url"],
        agent_id="alice",
        secret=relay_server["alice_secret"],
    )
    yield t
    t.close()


@pytest.fixture
def bob_transport(relay_server):
    """HTTPTransport for agent 'bob'."""
    t = HTTPTransport(
        relay_url=relay_server["base_url"],
        agent_id="bob",
        secret=relay_server["bob_secret"],
    )
    yield t
    t.close()


# ==================== Tests ====================


def test_health_check(relay_server):
    """Relay server responds to GET /api/v1/health."""
    resp = httpx.get(f"{relay_server['base_url']}/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_send_via_http(alice_transport):
    """HTTPTransport.send() delivers a message to the relay spool."""
    msg = MessageV2.create(from_agent="alice", to_agent="bob", body="http-send-test")
    msg_id = alice_transport.send(msg)
    assert msg_id == msg.id
    assert msg_id.startswith("msg_")


def test_poll_via_http(alice_transport, bob_transport):
    """HTTPTransport.poll() retrieves the message from the relay."""
    msg = MessageV2.create(from_agent="alice", to_agent="bob", body="http-poll-test")
    alice_transport.send(msg)

    messages = bob_transport.poll("bob", limit=10, lease_seconds=5)
    bodies = [m.body for m in messages]
    assert "http-poll-test" in bodies


def test_ack_via_http(alice_transport, bob_transport):
    """HTTPTransport.ack() marks the message as delivered."""
    msg = MessageV2.create(from_agent="alice", to_agent="bob", body="http-ack-test")
    alice_transport.send(msg)

    messages = bob_transport.poll("bob", limit=10, lease_seconds=5)
    target = next(m for m in messages if m.body == "http-ack-test")

    result = bob_transport.ack(target.id, "bob")
    assert result is True

    # Acked message should not appear in next poll
    messages_after = bob_transport.poll("bob", limit=10, lease_seconds=5)
    ids_after = [m.id for m in messages_after]
    assert target.id not in ids_after


def test_nack_via_http(alice_transport, bob_transport):
    """HTTPTransport.nack() requeues the message for retry."""
    msg = MessageV2.create(from_agent="alice", to_agent="bob", body="http-nack-test")
    alice_transport.send(msg)

    messages = bob_transport.poll("bob", limit=10, lease_seconds=5)
    target = next(m for m in messages if m.body == "http-nack-test")

    result = bob_transport.nack(target.id, "bob", error="transient failure")
    assert result is True

    # After nack, message should be pollable again
    messages_after = bob_transport.poll("bob", limit=10, lease_seconds=5)
    ids_after = [m.id for m in messages_after]
    assert target.id in ids_after


def test_full_round_trip(alice_transport, bob_transport):
    """Full lifecycle: send -> poll -> ack -> get_status, all through HTTP."""
    msg = MessageV2.create(from_agent="alice", to_agent="bob", body="http-roundtrip-test")
    msg_id = alice_transport.send(msg)

    # Poll
    messages = bob_transport.poll("bob", limit=10, lease_seconds=5)
    target = next(m for m in messages if m.id == msg_id)
    assert target.body == "http-roundtrip-test"
    assert target.from_agent == "alice"

    # Ack
    bob_transport.ack(msg_id, "bob")

    # Status check
    status = alice_transport.get_status(msg_id)
    assert status is not None


def test_conversation_threading_via_http(alice_transport, bob_transport):
    """conversation_id and reply_to are preserved through the relay."""
    msg1 = MessageV2.create(
        from_agent="alice", to_agent="bob",
        body="http-thread-start", conversation_id="conv-http-001",
    )
    alice_transport.send(msg1)

    polled = bob_transport.poll("bob", limit=10, lease_seconds=5)
    target = next(m for m in polled if m.body == "http-thread-start")
    assert target.routing.conversation_id == "conv-http-001"
    bob_transport.ack(target.id, "bob")

    # Reply with same conversation_id and reply_to
    msg2 = MessageV2.create(
        from_agent="bob", to_agent="alice",
        body="http-thread-reply", conversation_id="conv-http-001",
        reply_to=msg1.id,
    )
    bob_transport.send(msg2)

    replies = alice_transport.poll("alice", limit=10, lease_seconds=5)
    reply = next(m for m in replies if m.body == "http-thread-reply")
    assert reply.routing.conversation_id == "conv-http-001"
    assert reply.routing.reply_to == msg1.id


def test_priority_ordering_via_http(alice_transport, bob_transport):
    """Relay spool respects priority ordering through HTTP."""
    for prio, body in [
        (Priority.LOW, "http-prio-low"),
        (Priority.URGENT, "http-prio-urgent"),
        (Priority.NORMAL, "http-prio-normal"),
    ]:
        msg = MessageV2.create(
            from_agent="alice", to_agent="bob",
            body=body, priority=prio,
        )
        alice_transport.send(msg)

    messages = bob_transport.poll("bob", limit=20, lease_seconds=5)
    prio_bodies = [m.body for m in messages if m.body.startswith("http-prio-")]
    assert prio_bodies.index("http-prio-urgent") < prio_bodies.index("http-prio-normal")
    assert prio_bodies.index("http-prio-normal") < prio_bodies.index("http-prio-low")


def test_multiple_agents_isolation(alice_transport, bob_transport):
    """Two agents with different secrets see only their own messages."""
    msg_to_bob = MessageV2.create(
        from_agent="alice", to_agent="bob", body="http-isolation-for-bob",
    )
    alice_transport.send(msg_to_bob)

    msg_to_alice = MessageV2.create(
        from_agent="bob", to_agent="alice", body="http-isolation-for-alice",
    )
    bob_transport.send(msg_to_alice)

    bob_msgs = bob_transport.poll("bob", limit=20, lease_seconds=5)
    bob_bodies = [m.body for m in bob_msgs]
    assert "http-isolation-for-bob" in bob_bodies
    assert "http-isolation-for-alice" not in bob_bodies

    alice_msgs = alice_transport.poll("alice", limit=20, lease_seconds=5)
    alice_bodies = [m.body for m in alice_msgs]
    assert "http-isolation-for-alice" in alice_bodies
    assert "http-isolation-for-bob" not in alice_bodies


def test_auth_rejection(relay_server):
    """Wrong secret gets 401 from the relay."""
    wrong_secret = os.urandom(32)
    bad_transport = HTTPTransport(
        relay_url=relay_server["base_url"],
        agent_id="alice",
        secret=wrong_secret,
    )
    try:
        msg = MessageV2.create(from_agent="alice", to_agent="bob", body="should-fail")
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            bad_transport.send(msg)
        assert exc_info.value.response.status_code == 401
    finally:
        bad_transport.close()
