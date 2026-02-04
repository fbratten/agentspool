"""Transport abstraction for agent communication.

Provides a pluggable transport layer:
- SQLiteTransport (primary) — durable message queue with WAL
- HTTPTransport (cross-device) — HMAC-authenticated HTTP relay
- FileTransport (debug/compat) — JSON files for debugging
"""

from abc import ABC, abstractmethod
from typing import Optional

from agent_comm.message_types import MessageV2

__all__ = [
    "Transport",
    "SQLiteTransport",
    "HTTPTransport",
    "FileTransport",
]


# Lazy imports to avoid circular dependencies and unnecessary deps
def __getattr__(name):
    if name == "SQLiteTransport":
        from agent_comm.transports.sqlite_transport import SQLiteTransport
        return SQLiteTransport
    if name == "HTTPTransport":
        from agent_comm.transports.http_transport import HTTPTransport
        return HTTPTransport
    if name == "FileTransport":
        from agent_comm.transports.file_transport import FileTransport
        return FileTransport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class Transport(ABC):
    """Abstract base class for message transport."""

    @abstractmethod
    def send(self, message: MessageV2) -> str:
        """Send a message. Returns the message ID."""
        ...

    @abstractmethod
    def poll(self, agent_id: str, limit: int = 10, lease_seconds: int = 60) -> list[MessageV2]:
        """Poll for messages addressed to agent_id. Claims them with a lease."""
        ...

    @abstractmethod
    def ack(self, message_id: str, agent_id: str) -> bool:
        """Acknowledge successful processing of a message."""
        ...

    @abstractmethod
    def nack(self, message_id: str, agent_id: str, error: Optional[str] = None) -> bool:
        """Negative-acknowledge a message (mark as failed or requeue)."""
        ...

    @abstractmethod
    def get_status(self, message_id: str) -> Optional[dict]:
        """Get delivery status for a message."""
        ...
