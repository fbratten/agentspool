"""Configuration for agent-comm."""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class AgentCommConfig:
    """Configuration for the agent communication system."""

    # Project root
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent)

    # SQLite spool
    db_path: Path = field(default=None)

    # File transport (debug mode)
    file_inbox_root: Path = field(default=None)

    # Default transport
    default_transport: str = "sqlite"  # "sqlite", "file", or "http"

    # Message defaults
    default_ttl_hours: int = 24
    default_max_attempts: int = 3
    default_lease_seconds: int = 60

    # Protocol version
    protocol_version: str = "2.0"

    # Relay settings (for HTTP transport)
    relay_secrets_path: Path = field(default=None)

    def __post_init__(self):
        if self.db_path is None:
            self.db_path = self.project_root / "data" / "coordination.db"
        if self.file_inbox_root is None:
            self.file_inbox_root = self.project_root / "data" / "inboxes"
        if self.relay_secrets_path is None:
            self.relay_secrets_path = self.project_root / "data" / "relay-secrets.json"
