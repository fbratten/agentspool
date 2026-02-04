"""Core coordinator — ties together registry, transport, and message protocol."""

import logging
from pathlib import Path
from typing import Optional

from agent_comm.config import AgentCommConfig
from agent_comm.message_types import MessageV2, PayloadType, Priority
from agent_comm.registry import AgentProfile, LocalRegistry
from agent_comm.transports import Transport
from agent_comm.transports.sqlite_transport import SQLiteTransport

logger = logging.getLogger(__name__)


class Coordinator:
    """Central coordinator for N-agent communication.

    Manages agent registration, message routing, and transport delegation.
    Supports hybrid routing: local agents use SQLiteTransport, remote agents
    use HTTPTransport (resolved per-message based on registry profile).
    """

    def __init__(self, config: Optional[AgentCommConfig] = None):
        self.config = config or AgentCommConfig()
        self.registry = LocalRegistry(self.config.project_root / "data" / "registry.json")
        self._transport: Optional[Transport] = None
        # Cache key is (relay_url, sender_id) to use correct HMAC secret per sender
        self._http_transports: dict[tuple[str, str], Transport] = {}

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            if self.config.default_transport == "sqlite":
                self._transport = SQLiteTransport(self.config.db_path)
            else:
                from agent_comm.transports.file_transport import FileTransport
                self._transport = FileTransport(self.config.file_inbox_root)
        return self._transport

    def _get_transport_for_agent(self, to_agent: str, from_agent: str) -> Transport:
        """Resolve the transport for a target agent.

        Looks up the agent's profile in the registry. If the agent uses HTTP
        transport and has a relay_url in metadata, returns an HTTPTransport
        instance (cached by URL + sender). Otherwise falls back to the default
        local transport.

        Args:
            to_agent: The recipient agent ID (for profile lookup)
            from_agent: The sender agent ID (for HMAC signing identity)
        """
        profile = self.registry.get(to_agent)
        if profile and profile.transport == "http":
            relay_url = profile.metadata.get("relay_url")
            if relay_url:
                return self._get_or_create_http_transport(relay_url, from_agent)
        return self.transport

    def _get_or_create_http_transport(self, relay_url: str, sender_id: str) -> Transport:
        """Get or create a cached HTTPTransport for a relay URL and sender.

        Security: Each sender gets its own transport with its own HMAC secret.
        The cache key is (relay_url, sender_id) to ensure correct signing.

        Args:
            relay_url: The relay server URL
            sender_id: The sending agent's ID (for HMAC signing)
        """
        cache_key = (relay_url, sender_id)
        if cache_key not in self._http_transports:
            from agent_comm.relay.auth import SecretStore
            from agent_comm.transports.http_transport import HTTPTransport

            secrets = SecretStore(self.config.relay_secrets_path)
            secret = secrets.get(sender_id)
            if not secret:
                logger.warning(
                    f"No relay secret for sender '{sender_id}', falling back to local transport"
                )
                return self.transport

            self._http_transports[cache_key] = HTTPTransport(
                relay_url=relay_url,
                agent_id=sender_id,
                secret=secret,
            )
        return self._http_transports[cache_key]

    def register_agent(
        self,
        agent_id: str,
        capabilities: Optional[list[str]] = None,
        transport: str = "sqlite",
        device: str = "local",
        metadata: Optional[dict] = None,
    ) -> AgentProfile:
        """Register an agent in the local registry."""
        profile = AgentProfile(
            agent_id=agent_id,
            capabilities=capabilities or [],
            transport=transport,
            device=device,
            metadata=metadata or {},
        )
        return self.registry.register(profile)

    def send(
        self,
        from_agent: str,
        to_agent: str,
        body: str,
        subject: Optional[str] = None,
        payload_type: Optional[PayloadType] = None,
        payload_data: Optional[dict] = None,
        reply_to: Optional[str] = None,
        conversation_id: Optional[str] = None,
        priority: Priority = Priority.NORMAL,
        ttl_hours: Optional[int] = None,
    ) -> str:
        """Send a message from one agent to another. Returns message ID.

        Automatically routes to HTTPTransport for remote agents or
        SQLiteTransport for local agents based on the recipient's profile.
        """
        message = MessageV2.create(
            from_agent=from_agent,
            to_agent=to_agent,
            body=body,
            subject=subject,
            payload_type=payload_type,
            payload_data=payload_data,
            reply_to=reply_to,
            conversation_id=conversation_id,
            priority=priority,
            ttl_hours=ttl_hours or self.config.default_ttl_hours,
        )
        # Pass from_agent to select correct HMAC secret for HTTP transport
        transport = self._get_transport_for_agent(to_agent, from_agent)
        return transport.send(message)

    def poll(
        self,
        agent_id: str,
        limit: int = 10,
        lease_seconds: Optional[int] = None,
    ) -> list[MessageV2]:
        """Poll for messages addressed to an agent (always uses local transport)."""
        return self.transport.poll(
            agent_id,
            limit=limit,
            lease_seconds=lease_seconds or self.config.default_lease_seconds,
        )

    def ack(self, message_id: str, agent_id: str) -> bool:
        """Acknowledge a message (always uses local transport)."""
        return self.transport.ack(message_id, agent_id)

    def nack(self, message_id: str, agent_id: str, error: Optional[str] = None) -> bool:
        """Negative-acknowledge a message (always uses local transport)."""
        return self.transport.nack(message_id, agent_id, error)

    def discover(self, capability: Optional[str] = None) -> list[AgentProfile]:
        """Discover registered agents, optionally by capability."""
        return self.registry.discover(capability)

    def status(self, message_id: str) -> Optional[dict]:
        """Get delivery status for a message."""
        return self.transport.get_status(message_id)

    def get_minna_registration_calls(self, agent_id: str) -> list[dict]:
        """Get Minna MCP tool calls needed to register an agent.

        Returns the calls as dicts — the caller (CLI/agent) executes them.
        """
        profile = self.registry.get(agent_id)
        if not profile:
            return []
        return profile.to_minna_calls()

    def close(self):
        if self._transport and hasattr(self._transport, "close"):
            self._transport.close()
        for t in self._http_transports.values():
            if hasattr(t, "close"):
                t.close()
        self._http_transports.clear()
