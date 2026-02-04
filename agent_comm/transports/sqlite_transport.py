"""SQLite transport — primary transport using the message spool."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_comm.message_types import (
    DeliveryStatus,
    MessageMetadata,
    MessagePayload,
    MessageRouting,
    MessageV2,
    PayloadType,
    Priority,
)
from agent_comm.spool import MessageSpool
from agent_comm.transports import Transport


class SQLiteTransport(Transport):
    """Primary transport: reads/writes to SQLite spool (coordination.db)."""

    def __init__(self, db_path: Path, check_same_thread: bool = True):
        self.spool = MessageSpool(db_path, check_same_thread=check_same_thread)

    def send(self, message: MessageV2) -> str:
        self.spool.insert(message)
        return message.id

    def poll(self, agent_id: str, limit: int = 10, lease_seconds: int = 60) -> list[MessageV2]:
        raw_messages = self.spool.poll(agent_id, limit, lease_seconds)
        return [self._row_to_message(row) for row in raw_messages]

    def ack(self, message_id: str, agent_id: str) -> bool:
        return self.spool.ack(message_id, agent_id)

    def nack(self, message_id: str, agent_id: str, error: Optional[str] = None) -> bool:
        return self.spool.nack(message_id, agent_id, error)

    def get_status(self, message_id: str) -> Optional[dict]:
        deliveries = self.spool.get_delivery_status(message_id)
        if not deliveries:
            return None
        return {
            "message_id": message_id,
            "deliveries": deliveries,
        }

    @staticmethod
    def _row_to_message(row: dict) -> MessageV2:
        """Convert a SQLite row dict back to MessageV2."""
        payload = None
        if row.get("payload_type"):
            payload_data = json.loads(row["payload_data"]) if row.get("payload_data") else {}
            payload = MessagePayload(type=PayloadType(row["payload_type"]), data=payload_data)

        metadata = MessageMetadata()
        if row.get("metadata"):
            meta_dict = json.loads(row["metadata"])
            metadata = MessageMetadata(**meta_dict)

        return MessageV2(
            id=row["id"],
            version=row.get("version", "2.0"),
            **{"from": row["from_agent"], "to": row["to_agent"]},
            subject=row.get("subject"),
            body=row.get("body"),
            timestamp=datetime.fromisoformat(row["created_at"]),
            priority=Priority(row.get("priority", "normal")),
            routing=MessageRouting(
                reply_to=row.get("reply_to"),
                conversation_id=row.get("conversation_id"),
            ),
            payload=payload,
            metadata=metadata,
        )

    def close(self):
        self.spool.close()
