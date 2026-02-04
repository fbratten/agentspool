"""HTTP relay for cross-device agent communication.

Provides:
- FastAPI relay server with HMAC authentication
- SecretStore for per-agent shared secrets
- NonceTracker for replay protection
"""

from agent_comm.relay.auth import HMACSigner, NonceTracker, SecretStore
from agent_comm.relay.config import RelayConfig

__all__ = [
    "HMACSigner",
    "NonceTracker",
    "SecretStore",
    "RelayConfig",
]
