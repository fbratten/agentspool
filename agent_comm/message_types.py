"""MessageV2 protocol — Pydantic models for inter-agent communication."""

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PayloadType(str, Enum):
    """Typed payload categories for agent messages."""

    TEXT = "text"
    TASK_ASSIGNMENT = "task_assignment"
    TASK_RESULT = "task_result"
    CONTEXT_SHARE = "context_share"
    STATUS_REQUEST = "status_request"
    STATUS_RESPONSE = "status_response"
    CAPABILITY_QUERY = "capability_query"
    CAPABILITY_RESPONSE = "capability_response"
    BROADCAST = "broadcast"


class Priority(str, Enum):
    """Message priority levels."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class DeliveryStatus(str, Enum):
    """Delivery state machine: queued → leased → acked/failed."""

    QUEUED = "queued"
    LEASED = "leased"
    ACKED = "acked"
    FAILED = "failed"


class MessageRouting(BaseModel):
    """Routing metadata for conversation threading and TTL."""

    reply_to: Optional[str] = None
    conversation_id: Optional[str] = None
    ttl_hours: int = 24


class MessagePayload(BaseModel):
    """Typed payload with arbitrary data."""

    type: PayloadType
    data: dict[str, Any] = Field(default_factory=dict)


class MessageMetadata(BaseModel):
    """Optional metadata for tracing and context."""

    spine_trace_id: Optional[str] = None
    source_project: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


def generate_message_id() -> str:
    """Generate a unique message ID: msg_YYYYMMDD_HHMMSS_hash6."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    # 6-char hash from timestamp + microseconds for uniqueness
    raw = f"{ts}_{now.microsecond}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:6]
    return f"msg_{ts}_{h}"


class MessageV2(BaseModel):
    """Version 2 inter-agent message format.

    Immutable envelope with typed payload, routing, and metadata.
    """

    id: str = Field(default_factory=generate_message_id)
    version: str = "2.0"
    from_agent: str = Field(..., alias="from")
    to_agent: str = Field(..., alias="to")
    subject: Optional[str] = None
    body: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    priority: Priority = Priority.NORMAL
    routing: MessageRouting = Field(default_factory=MessageRouting)
    payload: Optional[MessagePayload] = None
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)

    model_config = {"populate_by_name": True}

    def to_canonical_json(self) -> str:
        """Serialize to canonical JSON (sorted keys, no whitespace) for signing."""
        d = self.model_dump(mode="json", by_alias=True)
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    def to_json(self, indent: int = 2) -> str:
        """Serialize to human-readable JSON."""
        return self.model_dump_json(indent=indent, by_alias=True)

    @classmethod
    def from_json(cls, raw: str) -> "MessageV2":
        """Deserialize from JSON string."""
        return cls.model_validate_json(raw)

    @classmethod
    def create(
        cls,
        from_agent: str,
        to_agent: str,
        body: str,
        subject: Optional[str] = None,
        payload_type: Optional[PayloadType] = None,
        payload_data: Optional[dict] = None,
        reply_to: Optional[str] = None,
        conversation_id: Optional[str] = None,
        priority: Priority = Priority.NORMAL,
        ttl_hours: int = 24,
    ) -> "MessageV2":
        """Convenience factory for creating messages."""
        routing = MessageRouting(
            reply_to=reply_to,
            conversation_id=conversation_id,
            ttl_hours=ttl_hours,
        )
        payload = None
        if payload_type is not None:
            payload = MessagePayload(
                type=payload_type,
                data=payload_data or {},
            )
        return cls(
            **{"from": from_agent, "to": to_agent},
            subject=subject,
            body=body,
            priority=priority,
            routing=routing,
            payload=payload,
        )
