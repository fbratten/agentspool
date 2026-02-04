"""SQLite message spool with WAL mode, lease/ack semantics, and idempotency.

This is the durable message queue for agent-comm. Separate from Minna Memory
(which handles semantic registry/context). This handles mechanical message delivery.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from agent_comm.message_types import DeliveryStatus, MessageV2


class MessageSpool:
    """SQLite-backed message spool with queue semantics.

    Features:
    - WAL mode for concurrent read/write
    - Atomic claim (lease-based polling)
    - Idempotent insert (unique message_id + recipient)
    - Server-time TTL (expires_at set on insert)
    - Delivery tracking (queued → leased → acked/failed)
    """

    def __init__(self, db_path: Path, check_same_thread: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._check_same_thread = check_same_thread
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                isolation_level=None,  # autocommit for explicit transaction control
                check_same_thread=self._check_same_thread,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                version TEXT NOT NULL DEFAULT '2.0',
                from_agent TEXT NOT NULL,
                to_agent TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                priority TEXT DEFAULT 'normal',
                conversation_id TEXT,
                reply_to TEXT,
                payload_type TEXT,
                payload_data TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL REFERENCES messages(id),
                recipient_agent_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                lease_until TEXT,
                attempt INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(message_id, recipient_agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_deliveries_poll
                ON deliveries(recipient_agent_id, status, lease_until);

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id);

            CREATE INDEX IF NOT EXISTS idx_messages_expires
                ON messages(expires_at);
        """)

    def insert(self, message: MessageV2, ttl_hours: Optional[int] = None) -> bool:
        """Insert a message into the spool. Returns False if duplicate (idempotent).

        Uses server-time (DB clock) for created_at and expires_at.
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        ttl = ttl_hours if ttl_hours is not None else message.routing.ttl_hours
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

        payload_type = message.payload.type.value if message.payload else None
        payload_data = json.dumps(message.payload.data) if message.payload else None
        metadata = json.dumps(message.metadata.model_dump()) if message.metadata else None

        try:
            conn.execute("BEGIN")
            conn.execute(
                """INSERT INTO messages (id, version, from_agent, to_agent, subject, body,
                   priority, conversation_id, reply_to, payload_type, payload_data,
                   created_at, expires_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.id, message.version, message.from_agent, message.to_agent,
                    message.subject, message.body, message.priority.value,
                    message.routing.conversation_id, message.routing.reply_to,
                    payload_type, payload_data, now, expires, metadata,
                ),
            )
            conn.execute(
                """INSERT INTO deliveries (message_id, recipient_agent_id, status,
                   max_attempts, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?)""",
                (message.id, message.to_agent, 3, now, now),
            )
            conn.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return False

    def poll(self, agent_id: str, limit: int = 10, lease_seconds: int = 60) -> list[dict]:
        """Atomic claim: fetch queued messages for agent_id with lease.

        Returns list of message dicts. Messages are leased (not consumed) —
        must be acked or they'll be requeued after lease expires.
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Find claimable deliveries: queued, or leased but expired
            rows = conn.execute(
                """SELECT d.id as delivery_id, d.message_id, d.attempt
                   FROM deliveries d
                   JOIN messages m ON m.id = d.message_id
                   WHERE d.recipient_agent_id = ?
                     AND (d.status = 'queued' OR (d.status = 'leased' AND d.lease_until < ?))
                     AND (m.expires_at IS NULL OR m.expires_at > ?)
                     AND d.attempt < d.max_attempts
                   ORDER BY CASE m.priority
                              WHEN 'urgent' THEN 0
                              WHEN 'high'   THEN 1
                              WHEN 'normal' THEN 2
                              WHEN 'low'    THEN 3
                              ELSE 4
                            END ASC,
                            m.created_at ASC
                   LIMIT ?""",
                (agent_id, now, now, limit),
            ).fetchall()

            if not rows:
                conn.execute("COMMIT")
                return []

            delivery_ids = [r["delivery_id"] for r in rows]
            message_ids = [r["message_id"] for r in rows]

            # Atomic claim: update status to leased
            placeholders = ",".join("?" for _ in delivery_ids)
            conn.execute(
                f"""UPDATE deliveries
                    SET status = 'leased', lease_until = ?, attempt = attempt + 1, updated_at = ?
                    WHERE id IN ({placeholders})""",
                [lease_until, now] + delivery_ids,
            )
            conn.execute("COMMIT")

            # Fetch full messages preserving priority order from delivery query
            placeholders = ",".join("?" for _ in message_ids)
            messages = conn.execute(
                f"SELECT * FROM messages WHERE id IN ({placeholders})",
                message_ids,
            ).fetchall()

            # Re-order to match the priority-sorted message_ids list
            msg_map = {dict(m)["id"]: dict(m) for m in messages}
            return [msg_map[mid] for mid in message_ids if mid in msg_map]

        except Exception:
            conn.execute("ROLLBACK")
            raise

    def ack(self, message_id: str, agent_id: str) -> bool:
        """Acknowledge successful processing. Returns True if updated."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        result = conn.execute(
            """UPDATE deliveries SET status = 'acked', updated_at = ?
               WHERE message_id = ? AND recipient_agent_id = ? AND status = 'leased'""",
            (now, message_id, agent_id),
        )
        return result.rowcount > 0

    def nack(self, message_id: str, agent_id: str, error: Optional[str] = None) -> bool:
        """Negative-acknowledge. Requeues if attempts remain, else marks failed."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        row = conn.execute(
            """SELECT attempt, max_attempts FROM deliveries
               WHERE message_id = ? AND recipient_agent_id = ?""",
            (message_id, agent_id),
        ).fetchone()

        if not row:
            return False

        if row["attempt"] >= row["max_attempts"]:
            new_status = "failed"
        else:
            new_status = "queued"

        conn.execute(
            """UPDATE deliveries SET status = ?, last_error = ?, lease_until = NULL, updated_at = ?
               WHERE message_id = ? AND recipient_agent_id = ?""",
            (new_status, error, now, message_id, agent_id),
        )
        return True

    def get_delivery_status(self, message_id: str) -> list[dict]:
        """Get all delivery records for a message."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM deliveries WHERE message_id = ?",
            (message_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: str) -> list[dict]:
        """Get all messages in a conversation, ordered by creation time."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_expired(self) -> int:
        """Remove expired messages and their deliveries. Returns count removed."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN")
        try:
            expired = conn.execute(
                "SELECT id FROM messages WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            ).fetchall()
            if not expired:
                conn.execute("COMMIT")
                return 0

            ids = [r["id"] for r in expired]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM deliveries WHERE message_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
            conn.execute("COMMIT")
            return len(ids)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def stats(self) -> dict:
        """Get spool statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        by_status = conn.execute(
            "SELECT status, COUNT(*) as c FROM deliveries GROUP BY status"
        ).fetchall()
        return {
            "total_messages": total,
            "deliveries": {r["status"]: r["c"] for r in by_status},
        }

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
