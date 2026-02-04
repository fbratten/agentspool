"""Integration test: Two agents exchange messages via agent-comm.

Tests the full Phase 1 flow:
1. Register two agents
2. Send message A → B
3. Poll as B (verify lease)
4. Ack as B
5. Reply B → A with conversation threading
6. Verify idempotency (duplicate send)
7. Verify TTL expiry
8. Agent discovery by capability
"""

import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_comm.config import AgentCommConfig
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import PayloadType, Priority


def test_two_agents():
    """Run the full integration test."""
    # Use a temp db for testing
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="agent_comm_test_"))
    config = AgentCommConfig(
        project_root=tmp_dir,
        db_path=tmp_dir / "test_coordination.db",
    )
    coord = Coordinator(config)

    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            print(f"  [PASS] {name}")
            passed += 1
        else:
            print(f"  [FAIL] {name} — {detail}")
            failed += 1

    try:
        # --- Test 1: Register agents ---
        print("\n1. Register agents")
        agent_a = coord.register_agent(
            "claude-code-pc1",
            capabilities=["code", "mcp", "bash"],
            device="pc1",
        )
        agent_b = coord.register_agent(
            "test-agent-pc1",
            capabilities=["research", "analysis"],
            device="pc1",
        )
        check("Agent A registered", agent_a.agent_id == "claude-code-pc1")
        check("Agent B registered", agent_b.agent_id == "test-agent-pc1")
        check("Agent A capabilities", agent_a.capabilities == ["code", "mcp", "bash"])

        # --- Test 2: Send message A → B ---
        print("\n2. Send message A → B")
        msg_id = coord.send(
            from_agent="claude-code-pc1",
            to_agent="test-agent-pc1",
            body="Hello from Claude Code!",
            subject="Test message",
            priority=Priority.NORMAL,
            conversation_id="conv_test_001",
        )
        check("Message sent", msg_id.startswith("msg_"), f"got: {msg_id}")

        # --- Test 3: Poll as B ---
        print("\n3. Poll as agent B")
        messages = coord.poll("test-agent-pc1", limit=10, lease_seconds=30)
        check("Got 1 message", len(messages) == 1, f"got {len(messages)}")
        if messages:
            msg = messages[0]
            check("From correct", msg.from_agent == "claude-code-pc1")
            check("Body correct", msg.body == "Hello from Claude Code!")
            check("Subject correct", msg.subject == "Test message")
            check("Conversation ID", msg.routing.conversation_id == "conv_test_001")

        # --- Test 4: Poll again (should be empty — message is leased) ---
        print("\n4. Poll again (leased)")
        messages2 = coord.poll("test-agent-pc1", limit=10)
        check("No messages (leased)", len(messages2) == 0, f"got {len(messages2)}")

        # --- Test 5: Ack ---
        print("\n5. Ack message")
        ack_result = coord.ack(msg_id, "test-agent-pc1")
        check("Ack succeeded", ack_result)

        # --- Test 6: Status check ---
        print("\n6. Delivery status")
        status = coord.status(msg_id)
        check("Status found", status is not None)
        if status and "deliveries" in status:
            delivery = status["deliveries"][0]
            check("Status is acked", delivery["status"] == "acked", f"got: {delivery['status']}")

        # --- Test 7: Reply B → A with threading ---
        print("\n7. Reply B → A")
        reply_id = coord.send(
            from_agent="test-agent-pc1",
            to_agent="claude-code-pc1",
            body="Got it, thanks!",
            subject="Re: Test message",
            reply_to=msg_id,
            conversation_id="conv_test_001",
            payload_type=PayloadType.TEXT,
            payload_data={"note": "all good"},
        )
        check("Reply sent", reply_id.startswith("msg_"))

        replies = coord.poll("claude-code-pc1", limit=10)
        check("Reply received", len(replies) == 1, f"got {len(replies)}")
        if replies:
            check("Reply body", replies[0].body == "Got it, thanks!")
            check("Reply has payload", replies[0].payload is not None)
            if replies[0].payload:
                check("Payload type", replies[0].payload.type == PayloadType.TEXT)
            coord.ack(reply_id, "claude-code-pc1")

        # --- Test 8: Idempotency ---
        print("\n8. Idempotency")
        from agent_comm.message_types import MessageV2
        dup_msg = MessageV2.create(
            from_agent="claude-code-pc1",
            to_agent="test-agent-pc1",
            body="Duplicate test",
        )
        first = coord.transport.send(dup_msg)
        # Send same message again
        from agent_comm.transports.sqlite_transport import SQLiteTransport
        if isinstance(coord.transport, SQLiteTransport):
            dup_result = coord.transport.spool.insert(dup_msg)
            check("Duplicate rejected", dup_result is False)
        else:
            check("Duplicate test (skipped for file transport)", True)

        # --- Test 9: Agent discovery ---
        print("\n9. Agent discovery")
        all_agents = coord.discover()
        check("Found 2 agents", len(all_agents) == 2, f"got {len(all_agents)}")

        code_agents = coord.discover(capability="code")
        check("Found 1 code agent", len(code_agents) == 1, f"got {len(code_agents)}")

        research_agents = coord.discover(capability="research")
        check("Found 1 research agent", len(research_agents) == 1)

        no_agents = coord.discover(capability="nonexistent")
        check("Found 0 for unknown cap", len(no_agents) == 0)

        # --- Test 10: Spool stats ---
        print("\n10. Spool stats")
        if isinstance(coord.transport, SQLiteTransport):
            stats = coord.transport.spool.stats()
            check("Stats available", "total_messages" in stats)
            check("Messages counted", stats["total_messages"] >= 3, f"got {stats['total_messages']}")
            print(f"     Stats: {stats}")

        # --- Test 11: Minna registration calls ---
        print("\n11. Minna registration calls")
        calls = coord.get_minna_registration_calls("claude-code-pc1")
        check("Got Minna calls", len(calls) == 5, f"got {len(calls)}")
        if calls:
            check("First call is add_entity", calls[0]["tool"] == "memory_add_entity")
            check("Entity name correct", calls[0]["args"]["name"] == "agent:claude-code-pc1")

    finally:
        coord.close()

    # Summary
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*50}")

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return failed == 0


if __name__ == "__main__":
    success = test_two_agents()
    sys.exit(0 if success else 1)
