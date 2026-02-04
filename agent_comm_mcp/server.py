"""agent-comm MCP Server — Inter-agent communication via MCP tools.

Exposes 14 tools for agent messaging, discovery, and coordination.
Wraps the agent_comm.Coordinator with a lazy singleton pattern.

Usage:
    python -m agent_comm_mcp.server

Configuration via environment variables:
    AGENT_COMM_DB_PATH       — SQLite database path (default: ./data/coordination.db)
    AGENT_COMM_PROJECT_ROOT  — Project root for registry/config (default: agent-comm repo root)
    AGENT_COMM_TRANSPORT     — Default transport: sqlite|file (default: sqlite)
    AGENT_COMM_TTL_HOURS     — Default message TTL in hours (default: 24)
    AGENT_COMM_LEASE_SECONDS — Default lease duration in seconds (default: 60)
"""

import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from agent_comm.config import AgentCommConfig
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import PayloadType, Priority

# Initialize MCP server
mcp = FastMCP("agent-comm-mcp")

# ==================== Configuration ====================

_PROJECT_ROOT = Path(os.environ.get(
    "AGENT_COMM_PROJECT_ROOT",
    Path(__file__).parent.parent,
))

_DB_PATH = os.environ.get("AGENT_COMM_DB_PATH")
_TRANSPORT = os.environ.get("AGENT_COMM_TRANSPORT", "sqlite")
_TTL_HOURS = int(os.environ.get("AGENT_COMM_TTL_HOURS", "24"))
_LEASE_SECONDS = int(os.environ.get("AGENT_COMM_LEASE_SECONDS", "60"))

# ==================== Lazy Singleton ====================

_coordinator: Optional[Coordinator] = None


def get_coordinator() -> Coordinator:
    """Get or create the Coordinator singleton."""
    global _coordinator
    if _coordinator is None:
        config = AgentCommConfig(
            project_root=_PROJECT_ROOT,
            default_transport=_TRANSPORT,
            default_ttl_hours=_TTL_HOURS,
            default_lease_seconds=_LEASE_SECONDS,
        )
        if _DB_PATH:
            config.db_path = Path(_DB_PATH)
        _coordinator = Coordinator(config)
    return _coordinator


# ==================== Input Models ====================


class SendMessageInput(BaseModel):
    """Input for sending a message between agents."""
    model_config = ConfigDict(extra="forbid")

    from_agent: str = Field(..., description="Sender agent ID")
    to_agent: str = Field(..., description="Recipient agent ID")
    body: str = Field(..., description="Message body text")
    subject: Optional[str] = Field(default=None, description="Optional subject line")
    # Use enum types for proper Pydantic validation with clear error messages
    priority: Priority = Field(default=Priority.NORMAL, description="Priority: low, normal, high, urgent")
    payload_type: Optional[PayloadType] = Field(default=None,
        description="Payload type: text, task_assignment, task_result, context_share, "
                    "status_request, status_response, capability_query, capability_response, broadcast")
    payload_data: Optional[dict] = Field(default=None, description="Arbitrary payload data dict")
    reply_to: Optional[str] = Field(default=None, description="Message ID being replied to")
    conversation_id: Optional[str] = Field(default=None, description="Conversation thread ID")
    ttl_hours: Optional[int] = Field(default=None, description="Message TTL in hours (default: 24)")


class PollMessagesInput(BaseModel):
    """Input for polling messages for an agent."""
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Agent ID to poll messages for")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum messages to return")
    lease_seconds: Optional[int] = Field(default=None, description="Lease duration in seconds (default: 60)")


class AckMessageInput(BaseModel):
    """Input for acknowledging a message."""
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(..., description="Message ID to acknowledge")
    agent_id: str = Field(..., description="Agent ID acknowledging the message")


class NackMessageInput(BaseModel):
    """Input for negative-acknowledging a message."""
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(..., description="Message ID to nack")
    agent_id: str = Field(..., description="Agent ID nacking the message")
    error: Optional[str] = Field(default=None, description="Error description for diagnostics")


class RegisterAgentInput(BaseModel):
    """Input for registering an agent."""
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Unique agent identifier")
    capabilities: Optional[list[str]] = Field(default=None, description="Agent capabilities (e.g., ['code', 'research'])")
    transport: str = Field(default="sqlite", description="Transport type: sqlite, file, http")
    device: str = Field(default="local", description="Device identifier (e.g., pc1, pc2)")
    metadata: Optional[dict] = Field(default=None, description="Extra metadata (e.g., relay_url for HTTP transport)")


class DiscoverAgentsInput(BaseModel):
    """Input for discovering agents by capability."""
    model_config = ConfigDict(extra="forbid")

    capability: Optional[str] = Field(default=None, description="Filter by capability (omit for all agents)")


class HeartbeatInput(BaseModel):
    """Input for agent heartbeat."""
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Agent ID to heartbeat")


class DeregisterInput(BaseModel):
    """Input for deregistering an agent."""
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Agent ID to remove")


class MessageStatusInput(BaseModel):
    """Input for getting message delivery status."""
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(..., description="Message ID to check status for")


class ConversationInput(BaseModel):
    """Input for getting a conversation thread."""
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., description="Conversation thread ID")


class RelayGenSecretInput(BaseModel):
    """Input for generating a relay secret."""
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Agent ID to generate secret for")


# ==================== MCP Tools: Core Messaging ====================


@mcp.tool(
    name="comm_send",
    annotations={
        "title": "Send Message",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    },
)
def comm_send(params: SendMessageInput) -> dict:
    """Send a message from one agent to another.

    Creates a MessageV2 and routes it through the appropriate transport
    (SQLite for local agents, HTTP for remote agents with relay_url).
    Returns the generated message ID for tracking.

    Args:
        params: Message details (from, to, body, optional subject/priority/payload)

    Returns:
        dict with message_id and status
    """
    coord = get_coordinator()
    # No manual enum casts needed — Pydantic validates and converts input
    msg_id = coord.send(
        from_agent=params.from_agent,
        to_agent=params.to_agent,
        body=params.body,
        subject=params.subject,
        priority=params.priority,
        payload_type=params.payload_type,
        payload_data=params.payload_data,
        reply_to=params.reply_to,
        conversation_id=params.conversation_id,
        ttl_hours=params.ttl_hours,
    )
    return {"success": True, "message_id": msg_id}


@mcp.tool(
    name="comm_poll",
    annotations={
        "title": "Poll Messages",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    },
)
def comm_poll(params: PollMessagesInput) -> dict:
    """Poll for incoming messages addressed to an agent.

    Uses lease-based claiming: messages are leased (not consumed) and must
    be acknowledged via comm_ack. Unacknowledged messages are re-queued
    after the lease expires. Returns messages ordered by priority (urgent first).

    Args:
        params: Agent ID, limit, and optional lease duration

    Returns:
        dict with count and list of messages
    """
    coord = get_coordinator()
    messages = coord.poll(
        agent_id=params.agent_id,
        limit=params.limit,
        lease_seconds=params.lease_seconds,
    )
    return {
        "count": len(messages),
        "messages": [
            {
                "id": msg.id,
                "from": msg.from_agent,
                "to": msg.to_agent,
                "subject": msg.subject,
                "body": msg.body,
                "priority": msg.priority.value,
                "timestamp": msg.timestamp.isoformat(),
                "conversation_id": msg.routing.conversation_id,
                "reply_to": msg.routing.reply_to,
                "payload_type": msg.payload.type.value if msg.payload else None,
                "payload_data": msg.payload.data if msg.payload else None,
            }
            for msg in messages
        ],
    }


@mcp.tool(
    name="comm_ack",
    annotations={
        "title": "Acknowledge Message",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_ack(params: AckMessageInput) -> dict:
    """Acknowledge successful processing of a message.

    Marks the message as 'acked' in the delivery table. Only works on
    messages currently in 'leased' state (i.e., previously polled).

    Args:
        params: Message ID and agent ID

    Returns:
        dict with success status
    """
    coord = get_coordinator()
    result = coord.ack(params.message_id, params.agent_id)
    return {"success": result, "message_id": params.message_id}


@mcp.tool(
    name="comm_nack",
    annotations={
        "title": "Negative-Acknowledge Message",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_nack(params: NackMessageInput) -> dict:
    """Negative-acknowledge a message (report processing failure).

    If retry attempts remain, the message is re-queued for another attempt.
    If max attempts exceeded, it is marked as 'failed'. Optionally include
    an error description for diagnostics.

    Args:
        params: Message ID, agent ID, and optional error description

    Returns:
        dict with success status
    """
    coord = get_coordinator()
    result = coord.nack(params.message_id, params.agent_id, error=params.error)
    return {"success": result, "message_id": params.message_id}


# ==================== MCP Tools: Discovery & Registry ====================


@mcp.tool(
    name="comm_register_agent",
    annotations={
        "title": "Register Agent",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_register_agent(params: RegisterAgentInput) -> dict:
    """Register an agent in the local registry.

    Creates or updates an agent profile with capabilities, transport type,
    and device info. Re-registering an existing agent_id updates the profile.

    Args:
        params: Agent details (id, capabilities, transport, device, metadata)

    Returns:
        dict with the registered agent profile
    """
    coord = get_coordinator()
    profile = coord.register_agent(
        agent_id=params.agent_id,
        capabilities=params.capabilities,
        transport=params.transport,
        device=params.device,
        metadata=params.metadata,
    )
    return {
        "success": True,
        "agent": {
            "agent_id": profile.agent_id,
            "capabilities": profile.capabilities,
            "transport": profile.transport,
            "device": profile.device,
            "status": profile.status,
            "last_seen": profile.last_seen.isoformat(),
            "metadata": profile.metadata,
        },
    }


@mcp.tool(
    name="comm_discover_agents",
    annotations={
        "title": "Discover Agents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_discover_agents(params: DiscoverAgentsInput) -> dict:
    """Discover registered agents, optionally filtered by capability.

    Returns all agents matching the criteria, including their capabilities,
    transport type, device, and status.

    Args:
        params: Optional capability filter

    Returns:
        dict with count and list of agent profiles
    """
    coord = get_coordinator()
    agents = coord.discover(capability=params.capability)
    return {
        "count": len(agents),
        "agents": [
            {
                "agent_id": a.agent_id,
                "capabilities": a.capabilities,
                "transport": a.transport,
                "device": a.device,
                "status": a.status,
                "last_seen": a.last_seen.isoformat(),
                "metadata": a.metadata,
            }
            for a in agents
        ],
    }


@mcp.tool(
    name="comm_heartbeat",
    annotations={
        "title": "Agent Heartbeat",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_heartbeat(params: HeartbeatInput) -> dict:
    """Update an agent's last_seen timestamp and mark it active.

    Call periodically to indicate the agent is still alive.

    Args:
        params: Agent ID to heartbeat

    Returns:
        dict with success status
    """
    coord = get_coordinator()
    result = coord.registry.heartbeat(params.agent_id)
    return {"success": result, "agent_id": params.agent_id}


@mcp.tool(
    name="comm_deregister",
    annotations={
        "title": "Deregister Agent",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
    },
)
def comm_deregister(params: DeregisterInput) -> dict:
    """Remove an agent from the registry.

    Permanently deletes the agent profile. Messages already in the spool
    for this agent are NOT affected.

    Args:
        params: Agent ID to remove

    Returns:
        dict with success status
    """
    coord = get_coordinator()
    result = coord.registry.deregister(params.agent_id)
    return {"success": result, "agent_id": params.agent_id}


# ==================== MCP Tools: Status & Observability ====================


@mcp.tool(
    name="comm_message_status",
    annotations={
        "title": "Message Delivery Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_message_status(params: MessageStatusInput) -> dict:
    """Get delivery status for a specific message.

    Shows delivery records including status (queued/leased/acked/failed),
    attempt count, lease expiry, and last error.

    Args:
        params: Message ID to check

    Returns:
        dict with delivery details or not_found
    """
    coord = get_coordinator()
    info = coord.status(params.message_id)
    if info:
        return {"found": True, **info}
    return {"found": False, "message_id": params.message_id}


@mcp.tool(
    name="comm_spool_stats",
    annotations={
        "title": "Spool Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_spool_stats() -> dict:
    """Get message spool statistics.

    Returns total message count and delivery counts by status
    (queued, leased, acked, failed).

    Returns:
        dict with total_messages and deliveries breakdown
    """
    coord = get_coordinator()
    from agent_comm.transports.sqlite_transport import SQLiteTransport

    if isinstance(coord.transport, SQLiteTransport):
        stats = coord.transport.spool.stats()
        return {"success": True, **stats}
    return {"success": False, "error": "Stats only available for SQLite transport"}


@mcp.tool(
    name="comm_get_conversation",
    annotations={
        "title": "Get Conversation Thread",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_get_conversation(params: ConversationInput) -> dict:
    """Get all messages in a conversation thread, ordered by time.

    Retrieves the full message chain for a conversation_id. Useful for
    reviewing the history of a multi-message exchange between agents.

    Args:
        params: Conversation thread ID

    Returns:
        dict with count and list of messages (same format as comm_poll)
    """
    coord = get_coordinator()
    from agent_comm.transports.sqlite_transport import SQLiteTransport

    if isinstance(coord.transport, SQLiteTransport):
        raw_rows = coord.transport.spool.get_conversation(params.conversation_id)
        # Convert raw DB rows to MessageV2 objects, then format like comm_poll
        messages = [SQLiteTransport._row_to_message(row) for row in raw_rows]
        return {
            "count": len(messages),
            "conversation_id": params.conversation_id,
            "messages": [
                {
                    "id": msg.id,
                    "from": msg.from_agent,
                    "to": msg.to_agent,
                    "subject": msg.subject,
                    "body": msg.body,
                    "priority": msg.priority.value,
                    "timestamp": msg.timestamp.isoformat(),
                    "conversation_id": msg.routing.conversation_id,
                    "reply_to": msg.routing.reply_to,
                    "payload_type": msg.payload.type.value if msg.payload else None,
                    "payload_data": msg.payload.data if msg.payload else None,
                }
                for msg in messages
            ],
        }
    return {"success": False, "error": "Conversations only available for SQLite transport"}


# ==================== MCP Tools: Maintenance ====================


@mcp.tool(
    name="comm_cleanup",
    annotations={
        "title": "Cleanup Expired Messages",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
    },
)
def comm_cleanup() -> dict:
    """Remove expired messages and their delivery records.

    Deletes messages where expires_at has passed. Also removes
    associated delivery records. Returns the count of removed messages.

    Returns:
        dict with count of removed messages
    """
    coord = get_coordinator()
    from agent_comm.transports.sqlite_transport import SQLiteTransport

    if isinstance(coord.transport, SQLiteTransport):
        count = coord.transport.spool.cleanup_expired()
        return {"success": True, "removed_count": count}
    return {"success": False, "error": "Cleanup only available for SQLite transport"}


# ==================== MCP Tools: Relay Admin ====================


@mcp.tool(
    name="comm_relay_gen_secret",
    annotations={
        "title": "Generate Relay Secret",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    },
)
def comm_relay_gen_secret(params: RelayGenSecretInput) -> dict:
    """Generate an HMAC shared secret for relay authentication.

    Creates a random 32-byte secret for an agent and stores it in
    the relay secrets file. Required for agents using HTTP transport.

    Args:
        params: Agent ID to generate secret for

    Returns:
        dict with the hex-encoded secret
    """
    from agent_comm.relay.auth import SecretStore

    coord = get_coordinator()
    store = SecretStore(coord.config.relay_secrets_path)
    hex_secret = store.generate(params.agent_id)
    return {
        "success": True,
        "agent_id": params.agent_id,
        "secret_hex": hex_secret,
        "secrets_path": str(coord.config.relay_secrets_path),
    }


@mcp.tool(
    name="comm_relay_list_secrets",
    annotations={
        "title": "List Relay Secrets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
def comm_relay_list_secrets() -> dict:
    """List all agents that have configured relay secrets.

    Returns agent IDs only (not the secrets themselves) for
    diagnostic and administrative purposes.

    Returns:
        dict with count and list of agent IDs
    """
    from agent_comm.relay.auth import SecretStore

    coord = get_coordinator()
    store = SecretStore(coord.config.relay_secrets_path)
    agents = store.list_agents()
    return {"count": len(agents), "agents": agents}


# ==================== Server Entry Point ====================


def main() -> None:
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
