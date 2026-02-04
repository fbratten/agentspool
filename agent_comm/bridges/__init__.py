"""Bridge abstraction for connecting external agents to the spool.

A bridge polls the SQLite spool for messages addressed to an external agent,
forwards them via a transport-specific mechanism (subprocess, HTTP, etc.),
and inserts replies back into the spool.

Bridge lifecycle:
1. Poll spool for messages to the bridged agent
2. Forward each message to the external agent
3. Parse the response
4. Insert response as a reply message into the spool
5. Ack the original message
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from agent_comm.message_types import MessageV2

__all__ = [
    "Bridge",
    "BridgeResult",
    "OpenClawBridge",
    "OpenClawDirectBridge",
    "BridgeRunner",
]


# Lazy imports for concrete implementations
def __getattr__(name):
    if name == "OpenClawBridge":
        from agent_comm.bridges.openclaw_bridge import OpenClawBridge
        return OpenClawBridge
    if name == "OpenClawDirectBridge":
        from agent_comm.bridges.openclaw_direct_bridge import OpenClawDirectBridge
        return OpenClawDirectBridge
    if name == "BridgeRunner":
        from agent_comm.bridges.runner import BridgeRunner
        return BridgeRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class BridgeResult:
    """Result of forwarding a message to an external agent."""

    success: bool
    reply_text: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[str] = None


class Bridge(ABC):
    """Abstract base class for agent bridges.

    Bridges connect external agents (OpenClaw, HTTP endpoints, etc.)
    to the agent-comm spool. Each bridge handles one external agent.
    """

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """The agent ID this bridge handles (e.g., 'nelly-pc2')."""
        ...

    @abstractmethod
    def forward(self, message: MessageV2) -> BridgeResult:
        """Forward a message to the external agent and return the result.

        This is synchronous — blocks until the external agent responds
        or times out.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the external agent is reachable."""
        ...
