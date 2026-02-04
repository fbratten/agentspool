"""Configuration for the HTTP relay server."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RelayConfig:
    """Configuration for the relay server."""

    # Server binding
    host: str = "0.0.0.0"
    port: int = 8420

    # Project root (for resolving relative paths)
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)

    # Paths (set in __post_init__ if None)
    secrets_path: Path = field(default=None)
    db_path: Path = field(default=None)
    registry_path: Path = field(default=None)

    # Security
    replay_window_seconds: int = 300

    def __post_init__(self):
        if self.secrets_path is None:
            self.secrets_path = self.project_root / "data" / "relay-secrets.json"
        if self.db_path is None:
            self.db_path = self.project_root / "data" / "relay.db"
        if self.registry_path is None:
            self.registry_path = self.project_root / "data" / "relay-registry.json"
