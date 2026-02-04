"""Agent Registry — backed by Minna Memory (used as-is via MCP tools).

This module provides a local-first registry with optional Minna Memory
integration. When used from AI coding agents (with MCP access), the register/discover
calls will also write to Minna. When Minna is unavailable, falls back to
a local JSON file.

Note: Minna MCP calls are documented here but executed by the caller
(coordinator or CLI) since MCP tools are invoked at the agent level,
not from Python directly. This module manages the local fallback registry.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class AgentProfile(BaseModel):
    """Registered agent profile."""

    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    transport: str = "sqlite"  # "sqlite", "file", "http"
    device: str = "local"
    status: str = "active"  # "active", "inactive", "offline"
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = Field(default_factory=dict)

    def to_minna_calls(self) -> list[dict]:
        """Generate Minna MCP tool calls for registering this agent.

        Returns list of dicts describing the MCP calls to make:
        [
            {"tool": "memory_add_entity", "args": {...}},
            {"tool": "memory_store", "args": {...}},
            ...
        ]
        """
        entity_name = f"agent:{self.agent_id}"
        calls = [
            {
                "tool": "memory_add_entity",
                "args": {"name": entity_name, "entity_type": "concept"},
            },
            {
                "tool": "memory_store",
                "args": {
                    "entity": entity_name,
                    "attribute": "capabilities",
                    "value": ",".join(self.capabilities),
                },
            },
            {
                "tool": "memory_store",
                "args": {
                    "entity": entity_name,
                    "attribute": "transport",
                    "value": self.transport,
                },
            },
            {
                "tool": "memory_store",
                "args": {
                    "entity": entity_name,
                    "attribute": "device",
                    "value": self.device,
                },
            },
            {
                "tool": "memory_store",
                "args": {
                    "entity": entity_name,
                    "attribute": "status",
                    "value": self.status,
                },
            },
        ]
        return calls


class LocalRegistry:
    """Local JSON-based agent registry (fallback when Minna unavailable)."""

    def __init__(self, registry_path: Path):
        self.path = Path(registry_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentProfile] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            for agent_id, profile_data in data.items():
                self._agents[agent_id] = AgentProfile(**profile_data)

    def _save(self):
        """Atomically save registry to disk.

        Uses tempfile + os.replace for atomic writes, preventing corruption
        from concurrent access or crashes mid-write.
        """
        data = {aid: p.model_dump(mode="json") for aid, p in self._agents.items()}
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def register(self, profile: AgentProfile) -> AgentProfile:
        """Register or update an agent."""
        profile.last_seen = datetime.now(timezone.utc)
        self._agents[profile.agent_id] = profile
        self._save()
        return profile

    def get(self, agent_id: str) -> Optional[AgentProfile]:
        return self._agents.get(agent_id)

    def discover(self, capability: Optional[str] = None) -> list[AgentProfile]:
        """Find agents, optionally filtered by capability."""
        agents = list(self._agents.values())
        if capability:
            agents = [a for a in agents if capability in a.capabilities]
        return agents

    def heartbeat(self, agent_id: str) -> bool:
        """Update last_seen timestamp. Returns False if agent not found."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        agent.last_seen = datetime.now(timezone.utc)
        agent.status = "active"
        self._save()
        return True

    def deregister(self, agent_id: str) -> bool:
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._save()
            return True
        return False

    def list_all(self) -> list[AgentProfile]:
        return list(self._agents.values())
