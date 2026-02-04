"""Tests for agent_comm_mcp server — 14 MCP tool functions."""

import os
import tempfile
from pathlib import Path

import pytest

# Patch env vars before importing server module
_test_tmpdir = None


@pytest.fixture(autouse=True)
def mcp_env(tmp_path, monkeypatch):
    """Set up isolated environment for each test.

    Creates a temp project root with data/ directory and resets
    the coordinator singleton between tests.
    """
    import agent_comm_mcp.server as srv

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(srv, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(srv, "_DB_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setattr(srv, "_TRANSPORT", "sqlite")
    monkeypatch.setattr(srv, "_TTL_HOURS", 24)
    monkeypatch.setattr(srv, "_LEASE_SECONDS", 60)
    monkeypatch.setattr(srv, "_coordinator", None)

    yield tmp_path

    # Cleanup singleton
    if srv._coordinator is not None:
        srv._coordinator.close()
    srv._coordinator = None


# ==================== Import shortcuts ====================

from agent_comm_mcp.server import (
    comm_send,
    comm_poll,
    comm_ack,
    comm_nack,
    comm_register_agent,
    comm_discover_agents,
    comm_heartbeat,
    comm_deregister,
    comm_message_status,
    comm_spool_stats,
    comm_get_conversation,
    comm_cleanup,
    comm_relay_gen_secret,
    comm_relay_list_secrets,
    SendMessageInput,
    PollMessagesInput,
    AckMessageInput,
    NackMessageInput,
    RegisterAgentInput,
    DiscoverAgentsInput,
    HeartbeatInput,
    DeregisterInput,
    MessageStatusInput,
    ConversationInput,
    RelayGenSecretInput,
)


# ==================== Core Messaging ====================


class TestCommSend:
    def test_send_basic(self):
        result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="hello"
        ))
        assert result["success"] is True
        assert "message_id" in result
        assert len(result["message_id"]) > 0

    def test_send_with_priority_and_subject(self):
        result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="urgent!",
            subject="alert", priority="urgent"
        ))
        assert result["success"] is True

    def test_send_with_payload(self):
        result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="task for you",
            payload_type="task_assignment",
            payload_data={"task": "review PR #42"},
        ))
        assert result["success"] is True


class TestCommPoll:
    def test_poll_empty(self):
        result = comm_poll(PollMessagesInput(agent_id="bob"))
        assert result["count"] == 0
        assert result["messages"] == []

    def test_poll_returns_sent_message(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="hello bob"
        ))
        result = comm_poll(PollMessagesInput(agent_id="bob"))
        assert result["count"] == 1
        msg = result["messages"][0]
        assert msg["from"] == "alice"
        assert msg["to"] == "bob"
        assert msg["body"] == "hello bob"
        assert msg["priority"] == "normal"
        assert "id" in msg
        assert "timestamp" in msg

    def test_poll_with_limit(self):
        for i in range(5):
            comm_send(SendMessageInput(
                from_agent="alice", to_agent="bob", body=f"msg {i}"
            ))
        result = comm_poll(PollMessagesInput(agent_id="bob", limit=2))
        assert result["count"] == 2

    def test_poll_wrong_agent(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="for bob"
        ))
        result = comm_poll(PollMessagesInput(agent_id="charlie"))
        assert result["count"] == 0


class TestCommAck:
    def test_ack_leased_message(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="ack me"
        ))
        poll_result = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll_result["messages"][0]["id"]

        result = comm_ack(AckMessageInput(message_id=msg_id, agent_id="bob"))
        assert result["success"] is True
        assert result["message_id"] == msg_id

    def test_ack_not_leased(self):
        result = comm_ack(AckMessageInput(
            message_id="nonexistent", agent_id="bob"
        ))
        assert result["success"] is False


class TestCommNack:
    def test_nack_requeues(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="nack me"
        ))
        poll_result = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll_result["messages"][0]["id"]

        result = comm_nack(NackMessageInput(
            message_id=msg_id, agent_id="bob", error="processing failed"
        ))
        assert result["success"] is True

    def test_nack_not_leased(self):
        result = comm_nack(NackMessageInput(
            message_id="nonexistent", agent_id="bob"
        ))
        assert result["success"] is False


# ==================== Discovery & Registry ====================


class TestCommRegisterAgent:
    def test_register_basic(self):
        result = comm_register_agent(RegisterAgentInput(
            agent_id="agent-1", capabilities=["code", "research"]
        ))
        assert result["success"] is True
        agent = result["agent"]
        assert agent["agent_id"] == "agent-1"
        assert agent["capabilities"] == ["code", "research"]
        assert agent["transport"] == "sqlite"
        assert agent["device"] == "local"
        assert agent["status"] == "active"

    def test_register_with_metadata(self):
        result = comm_register_agent(RegisterAgentInput(
            agent_id="remote-1",
            transport="http",
            device="pc2",
            metadata={"relay_url": "http://relay.example.com"},
        ))
        assert result["success"] is True
        assert result["agent"]["transport"] == "http"
        assert result["agent"]["metadata"]["relay_url"] == "http://relay.example.com"


class TestCommDiscoverAgents:
    def test_discover_empty(self):
        result = comm_discover_agents(DiscoverAgentsInput())
        assert result["count"] == 0

    def test_discover_all(self):
        comm_register_agent(RegisterAgentInput(agent_id="a1", capabilities=["code"]))
        comm_register_agent(RegisterAgentInput(agent_id="a2", capabilities=["research"]))
        result = comm_discover_agents(DiscoverAgentsInput())
        assert result["count"] == 2

    def test_discover_by_capability(self):
        comm_register_agent(RegisterAgentInput(agent_id="a1", capabilities=["code"]))
        comm_register_agent(RegisterAgentInput(agent_id="a2", capabilities=["research"]))
        result = comm_discover_agents(DiscoverAgentsInput(capability="code"))
        assert result["count"] == 1
        assert result["agents"][0]["agent_id"] == "a1"


class TestCommHeartbeat:
    def test_heartbeat_registered(self):
        comm_register_agent(RegisterAgentInput(agent_id="agent-hb"))
        result = comm_heartbeat(HeartbeatInput(agent_id="agent-hb"))
        assert result["success"] is True

    def test_heartbeat_unknown(self):
        result = comm_heartbeat(HeartbeatInput(agent_id="unknown"))
        assert result["success"] is False


class TestCommDeregister:
    def test_deregister_existing(self):
        comm_register_agent(RegisterAgentInput(agent_id="agent-del"))
        result = comm_deregister(DeregisterInput(agent_id="agent-del"))
        assert result["success"] is True

        # Verify it's gone
        discover = comm_discover_agents(DiscoverAgentsInput())
        assert discover["count"] == 0

    def test_deregister_unknown(self):
        result = comm_deregister(DeregisterInput(agent_id="unknown"))
        assert result["success"] is False


# ==================== Status & Observability ====================


class TestCommMessageStatus:
    def test_status_existing(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="status check"
        ))
        msg_id = send_result["message_id"]
        result = comm_message_status(MessageStatusInput(message_id=msg_id))
        assert result["found"] is True

    def test_status_not_found(self):
        result = comm_message_status(MessageStatusInput(message_id="nonexistent"))
        assert result["found"] is False


class TestCommSpoolStats:
    def test_stats_empty(self):
        result = comm_spool_stats()
        assert result["success"] is True
        assert result["total_messages"] == 0

    def test_stats_after_send(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="hello"
        ))
        result = comm_spool_stats()
        assert result["success"] is True
        assert result["total_messages"] == 1


class TestCommGetConversation:
    def test_conversation_empty(self):
        result = comm_get_conversation(ConversationInput(conversation_id="conv-999"))
        assert result["count"] == 0
        assert result["conversation_id"] == "conv-999"

    def test_conversation_thread(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="msg 1",
            conversation_id="conv-1",
        ))
        comm_send(SendMessageInput(
            from_agent="bob", to_agent="alice", body="msg 2",
            conversation_id="conv-1",
        ))
        result = comm_get_conversation(ConversationInput(conversation_id="conv-1"))
        assert result["count"] == 2


class TestCommCleanup:
    def test_cleanup_no_expired(self):
        result = comm_cleanup()
        assert result["success"] is True
        assert result["removed_count"] == 0


# ==================== Relay Admin ====================


class TestCommRelayGenSecret:
    def test_gen_secret(self):
        result = comm_relay_gen_secret(RelayGenSecretInput(agent_id="relay-agent"))
        assert result["success"] is True
        assert result["agent_id"] == "relay-agent"
        assert len(result["secret_hex"]) == 64  # 32 bytes hex

    def test_gen_secret_appears_in_list(self):
        comm_relay_gen_secret(RelayGenSecretInput(agent_id="relay-a"))
        result = comm_relay_list_secrets()
        assert result["count"] == 1
        assert "relay-a" in result["agents"]


class TestCommRelayListSecrets:
    def test_list_empty(self):
        result = comm_relay_list_secrets()
        assert result["count"] == 0
        assert result["agents"] == []


# ==================== Behavioral Semantics ====================


def _manipulate_db(field, value, table, where_col, where_val):
    """Directly manipulate DB fields for lease/TTL tests without time.sleep()."""
    import agent_comm_mcp.server as srv
    coord = srv.get_coordinator()
    conn = coord.transport.spool._get_conn()
    conn.execute(
        f"UPDATE {table} SET {field} = ? WHERE {where_col} = ?",
        (value, where_val),
    )


def _get_delivery_row(message_id):
    """Get raw delivery row for a message."""
    import agent_comm_mcp.server as srv
    coord = srv.get_coordinator()
    conn = coord.transport.spool._get_conn()
    row = conn.execute(
        "SELECT * FROM deliveries WHERE message_id = ?", (message_id,)
    ).fetchone()
    return dict(row) if row else None


class TestConversationThreading:
    """Validate conversation_id and reply_to flow through send → poll → get_conversation."""

    def test_conversation_id_preserved_in_poll(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="start",
            conversation_id="conv-100",
        ))
        result = comm_poll(PollMessagesInput(agent_id="bob"))
        assert result["messages"][0]["conversation_id"] == "conv-100"

    def test_reply_to_preserved_in_poll(self):
        send1 = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="first",
            conversation_id="conv-200",
        ))
        msg1_id = send1["message_id"]
        comm_send(SendMessageInput(
            from_agent="bob", to_agent="alice", body="reply",
            conversation_id="conv-200", reply_to=msg1_id,
        ))
        result = comm_poll(PollMessagesInput(agent_id="alice"))
        assert result["messages"][0]["reply_to"] == msg1_id

    def test_get_conversation_returns_all_messages_ordered(self):
        for i in range(4):
            sender, receiver = ("alice", "bob") if i % 2 == 0 else ("bob", "alice")
            comm_send(SendMessageInput(
                from_agent=sender, to_agent=receiver,
                body=f"msg-{i}", conversation_id="conv-300",
            ))
        result = comm_get_conversation(ConversationInput(conversation_id="conv-300"))
        assert result["count"] == 4
        bodies = [m["body"] for m in result["messages"]]
        assert bodies == ["msg-0", "msg-1", "msg-2", "msg-3"]

    def test_conversations_are_isolated(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="in conv-A",
            conversation_id="conv-A",
        ))
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="in conv-B",
            conversation_id="conv-B",
        ))
        result_a = comm_get_conversation(ConversationInput(conversation_id="conv-A"))
        result_b = comm_get_conversation(ConversationInput(conversation_id="conv-B"))
        assert result_a["count"] == 1
        assert result_b["count"] == 1
        assert result_a["messages"][0]["body"] == "in conv-A"
        assert result_b["messages"][0]["body"] == "in conv-B"

    def test_multi_turn_reply_chain(self):
        r1 = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="turn-1",
            conversation_id="conv-chain",
        ))
        r2 = comm_send(SendMessageInput(
            from_agent="bob", to_agent="alice", body="turn-2",
            conversation_id="conv-chain", reply_to=r1["message_id"],
        ))
        r3 = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="turn-3",
            conversation_id="conv-chain", reply_to=r2["message_id"],
        ))
        conv = comm_get_conversation(ConversationInput(conversation_id="conv-chain"))
        assert conv["count"] == 3
        assert conv["messages"][2]["reply_to"] == r2["message_id"]

    def test_no_conversation_id_means_no_threading(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="standalone",
        ))
        result = comm_poll(PollMessagesInput(agent_id="bob"))
        assert result["messages"][0]["conversation_id"] is None


class TestLeaseExpiryAndRequeue:
    """Validate that expired leases are reclaimed on next poll."""

    def test_expired_lease_reclaimed(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="lease-test",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll1["count"] == 1
        msg_id = poll1["messages"][0]["id"]

        # Second poll returns nothing (message is leased)
        poll2 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll2["count"] == 0

        # Expire the lease by setting lease_until to the past
        _manipulate_db("lease_until", "2020-01-01T00:00:00+00:00",
                       "deliveries", "message_id", msg_id)

        # Now poll should reclaim it
        poll3 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll3["count"] == 1
        assert poll3["messages"][0]["id"] == msg_id

    def test_reclaimed_message_increments_attempt(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="attempt-test",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll1["messages"][0]["id"]
        row1 = _get_delivery_row(msg_id)
        assert row1["attempt"] == 1

        _manipulate_db("lease_until", "2020-01-01T00:00:00+00:00",
                       "deliveries", "message_id", msg_id)
        comm_poll(PollMessagesInput(agent_id="bob"))
        row2 = _get_delivery_row(msg_id)
        assert row2["attempt"] == 2

    def test_acked_message_not_reclaimed(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="ack-stays",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll1["messages"][0]["id"]
        comm_ack(AckMessageInput(message_id=msg_id, agent_id="bob"))

        # Even with expired lease, acked should not be reclaimed
        _manipulate_db("lease_until", "2020-01-01T00:00:00+00:00",
                       "deliveries", "message_id", msg_id)
        poll2 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll2["count"] == 0

    def test_max_attempts_prevents_reclaim(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="exhaust-attempts",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll1["messages"][0]["id"]

        # Set attempt to max_attempts (3) so reclaim is blocked
        _manipulate_db("attempt", 3, "deliveries", "message_id", msg_id)
        _manipulate_db("lease_until", "2020-01-01T00:00:00+00:00",
                       "deliveries", "message_id", msg_id)
        poll2 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll2["count"] == 0

    def test_active_lease_not_reclaimed(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="active-lease",
        ))
        comm_poll(PollMessagesInput(agent_id="bob"))
        # Lease is fresh (60s default), second poll should return nothing
        poll2 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll2["count"] == 0


class TestNackRetrySemantics:
    """Validate nack requeue, error preservation, and retry exhaustion."""

    def test_nack_requeues_message(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="retry-me",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll1["messages"][0]["id"]
        comm_nack(NackMessageInput(
            message_id=msg_id, agent_id="bob", error="transient failure"
        ))
        # After nack, message should be pollable again
        poll2 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll2["count"] == 1
        assert poll2["messages"][0]["id"] == msg_id

    def test_nack_preserves_error(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="error-check",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll1["messages"][0]["id"]
        comm_nack(NackMessageInput(
            message_id=msg_id, agent_id="bob", error="timeout on LLM call"
        ))
        row = _get_delivery_row(msg_id)
        assert row["last_error"] == "timeout on LLM call"

    def test_three_nacks_marks_failed(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="fail-after-3",
        ))
        msg_id = None
        for i in range(3):
            poll = comm_poll(PollMessagesInput(agent_id="bob"))
            assert poll["count"] == 1, f"Expected message on attempt {i+1}"
            msg_id = poll["messages"][0]["id"]
            comm_nack(NackMessageInput(
                message_id=msg_id, agent_id="bob", error=f"fail-{i+1}"
            ))

        # After 3 nacks, message should be failed and not pollable
        row = _get_delivery_row(msg_id)
        assert row["status"] == "failed"

    def test_failed_message_not_pollable(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="dead-letter",
        ))
        msg_id = None
        for i in range(3):
            poll = comm_poll(PollMessagesInput(agent_id="bob"))
            msg_id = poll["messages"][0]["id"]
            comm_nack(NackMessageInput(
                message_id=msg_id, agent_id="bob", error=f"err-{i}"
            ))
        # Failed message should not appear
        poll_final = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll_final["count"] == 0

    def test_nack_without_error_still_requeues(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="no-error-nack",
        ))
        poll1 = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll1["messages"][0]["id"]
        comm_nack(NackMessageInput(message_id=msg_id, agent_id="bob"))
        poll2 = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll2["count"] == 1


class TestTTLEnforcementAndCleanup:
    """Validate TTL expiry filters messages from poll and cleanup removes them."""

    def test_expired_message_not_pollable(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="expired-msg",
        ))
        msg_id = send_result["message_id"]
        # Expire the message
        _manipulate_db("expires_at", "2020-01-01T00:00:00+00:00",
                       "messages", "id", msg_id)
        poll = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll["count"] == 0

    def test_cleanup_removes_expired(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="to-be-cleaned",
        ))
        msg_id = send_result["message_id"]
        _manipulate_db("expires_at", "2020-01-01T00:00:00+00:00",
                       "messages", "id", msg_id)
        result = comm_cleanup()
        assert result["success"] is True
        assert result["removed_count"] == 1

    def test_cleanup_preserves_non_expired(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="still-valid",
        ))
        result = comm_cleanup()
        assert result["removed_count"] == 0
        stats = comm_spool_stats()
        assert stats["total_messages"] == 1

    def test_expired_not_reclaimed_even_with_expired_lease(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="double-expired",
        ))
        msg_id = send_result["message_id"]
        # Poll to lease it, then expire both
        comm_poll(PollMessagesInput(agent_id="bob"))
        _manipulate_db("expires_at", "2020-01-01T00:00:00+00:00",
                       "messages", "id", msg_id)
        _manipulate_db("lease_until", "2020-01-01T00:00:00+00:00",
                       "deliveries", "message_id", msg_id)
        poll = comm_poll(PollMessagesInput(agent_id="bob"))
        assert poll["count"] == 0

    def test_cleanup_cascades_to_deliveries(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="cascade-test",
        ))
        msg_id = send_result["message_id"]
        _manipulate_db("expires_at", "2020-01-01T00:00:00+00:00",
                       "messages", "id", msg_id)
        comm_cleanup()
        row = _get_delivery_row(msg_id)
        assert row is None


class TestPriorityOrdering:
    """Validate that poll returns messages ordered by priority then FIFO."""

    def test_urgent_before_normal(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="normal-msg",
            priority="normal",
        ))
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="urgent-msg",
            priority="urgent",
        ))
        result = comm_poll(PollMessagesInput(agent_id="bob", limit=10))
        assert result["count"] == 2
        assert result["messages"][0]["body"] == "urgent-msg"
        assert result["messages"][1]["body"] == "normal-msg"

    def test_all_four_priorities_ordered(self):
        """All four priority levels sort correctly: urgent > high > normal > low."""
        for prio in ["low", "normal", "high", "urgent"]:
            comm_send(SendMessageInput(
                from_agent="alice", to_agent="bob",
                body=f"prio-{prio}", priority=prio,
            ))
        result = comm_poll(PollMessagesInput(agent_id="bob", limit=10))
        bodies = [m["body"] for m in result["messages"]]
        assert bodies == ["prio-urgent", "prio-high", "prio-normal", "prio-low"]

    def test_same_priority_fifo(self):
        for i in range(3):
            comm_send(SendMessageInput(
                from_agent="alice", to_agent="bob",
                body=f"fifo-{i}", priority="normal",
            ))
        result = comm_poll(PollMessagesInput(agent_id="bob", limit=10))
        bodies = [m["body"] for m in result["messages"]]
        assert bodies == ["fifo-0", "fifo-1", "fifo-2"]

    def test_high_before_normal_before_low(self):
        """All levels distinguished regardless of insertion order."""
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="low-msg", priority="low",
        ))
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="high-msg", priority="high",
        ))
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="normal-msg", priority="normal",
        ))
        result = comm_poll(PollMessagesInput(agent_id="bob", limit=10))
        bodies = [m["body"] for m in result["messages"]]
        assert bodies == ["high-msg", "normal-msg", "low-msg"]


class TestMessageStatusLifecycle:
    """Validate status transitions through the delivery state machine."""

    def test_status_queued_after_send(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="status-queued",
        ))
        status = comm_message_status(MessageStatusInput(
            message_id=send_result["message_id"]
        ))
        assert status["found"] is True

    def test_status_after_poll_is_leased(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="status-leased",
        ))
        comm_poll(PollMessagesInput(agent_id="bob"))
        row = _get_delivery_row(send_result["message_id"])
        assert row["status"] == "leased"

    def test_status_after_ack_is_acked(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="status-acked",
        ))
        comm_poll(PollMessagesInput(agent_id="bob"))
        comm_ack(AckMessageInput(
            message_id=send_result["message_id"], agent_id="bob"
        ))
        row = _get_delivery_row(send_result["message_id"])
        assert row["status"] == "acked"

    def test_status_after_exhausted_nacks_is_failed(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="status-failed",
        ))
        msg_id = send_result["message_id"]
        for _ in range(3):
            comm_poll(PollMessagesInput(agent_id="bob"))
            comm_nack(NackMessageInput(message_id=msg_id, agent_id="bob"))
        row = _get_delivery_row(msg_id)
        assert row["status"] == "failed"


class TestErrorHandlingAndEdgeCases:
    """Edge cases: wrong agent, double ack, all payload types, registry operations."""

    def test_ack_wrong_agent_fails(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="wrong-acker",
        ))
        poll = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll["messages"][0]["id"]
        result = comm_ack(AckMessageInput(message_id=msg_id, agent_id="charlie"))
        assert result["success"] is False

    def test_nack_wrong_agent_fails(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="wrong-nacker",
        ))
        poll = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll["messages"][0]["id"]
        # nack returns True if delivery row exists (checks message_id + agent_id)
        result = comm_nack(NackMessageInput(message_id=msg_id, agent_id="charlie"))
        assert result["success"] is False

    def test_double_ack_is_noop(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="double-ack",
        ))
        poll = comm_poll(PollMessagesInput(agent_id="bob"))
        msg_id = poll["messages"][0]["id"]
        result1 = comm_ack(AckMessageInput(message_id=msg_id, agent_id="bob"))
        assert result1["success"] is True
        # Second ack should fail (status is now 'acked', not 'leased')
        result2 = comm_ack(AckMessageInput(message_id=msg_id, agent_id="bob"))
        assert result2["success"] is False

    def test_all_payload_types_roundtrip(self):
        payload_types = [
            "text", "task_assignment", "task_result", "context_share",
            "status_request", "status_response", "capability_query",
            "capability_response", "broadcast",
        ]
        for pt in payload_types:
            result = comm_send(SendMessageInput(
                from_agent="alice", to_agent="bob", body=f"payload-{pt}",
                payload_type=pt, payload_data={"key": pt},
            ))
            assert result["success"] is True, f"Failed for payload_type={pt}"

        poll = comm_poll(PollMessagesInput(agent_id="bob", limit=20))
        assert poll["count"] == len(payload_types)
        received_types = {m["payload_type"] for m in poll["messages"]}
        assert received_types == set(payload_types)

    def test_deregister_does_not_affect_pending_messages(self):
        comm_register_agent(RegisterAgentInput(
            agent_id="temp-agent", capabilities=["test"],
        ))
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="temp-agent", body="pre-dereg",
        ))
        comm_deregister(DeregisterInput(agent_id="temp-agent"))
        # Messages in spool should still be pollable
        poll = comm_poll(PollMessagesInput(agent_id="temp-agent"))
        assert poll["count"] == 1

    def test_heartbeat_updates_last_seen(self):
        comm_register_agent(RegisterAgentInput(agent_id="hb-agent"))
        agents1 = comm_discover_agents(DiscoverAgentsInput())
        ts1 = agents1["agents"][0]["last_seen"]

        import time
        time.sleep(0.05)  # Small delay to ensure timestamp differs
        comm_heartbeat(HeartbeatInput(agent_id="hb-agent"))

        agents2 = comm_discover_agents(DiscoverAgentsInput())
        ts2 = agents2["agents"][0]["last_seen"]
        assert ts2 >= ts1


class TestSpoolStatsAccuracy:
    """Validate that spool stats reflect actual delivery states."""

    def test_stats_reflect_leased_state(self):
        comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="stats-leased",
        ))
        comm_poll(PollMessagesInput(agent_id="bob"))
        stats = comm_spool_stats()
        assert stats["total_messages"] == 1
        assert stats["deliveries"].get("leased", 0) == 1

    def test_stats_reflect_mixed_states(self):
        # Send 3 messages
        ids = []
        for i in range(3):
            r = comm_send(SendMessageInput(
                from_agent="alice", to_agent="bob", body=f"mixed-{i}",
            ))
            ids.append(r["message_id"])

        # Poll all, ack first, nack-to-fail second, leave third leased
        comm_poll(PollMessagesInput(agent_id="bob", limit=10))
        comm_ack(AckMessageInput(message_id=ids[0], agent_id="bob"))

        # Exhaust retries on second: set attempt to max and nack
        _manipulate_db("attempt", 3, "deliveries", "message_id", ids[1])
        comm_nack(NackMessageInput(message_id=ids[1], agent_id="bob"))

        stats = comm_spool_stats()
        assert stats["total_messages"] == 3
        assert stats["deliveries"].get("acked", 0) == 1
        assert stats["deliveries"].get("failed", 0) == 1
        assert stats["deliveries"].get("leased", 0) == 1

    def test_stats_after_cleanup(self):
        send_result = comm_send(SendMessageInput(
            from_agent="alice", to_agent="bob", body="cleanup-stats",
        ))
        _manipulate_db("expires_at", "2020-01-01T00:00:00+00:00",
                       "messages", "id", send_result["message_id"])
        comm_cleanup()
        stats = comm_spool_stats()
        assert stats["total_messages"] == 0
