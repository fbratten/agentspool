"""File transport — debug/compatibility mode using JSON files."""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_comm.message_types import MessageV2
from agent_comm.transports import Transport

logger = logging.getLogger(__name__)


class FileTransport(Transport):
    """Debug/compatibility transport: reads/writes JSON files to inbox directories.

    Structure:
        root/
        ├── {agent_id}/
        │   ├── inbox/          # Pending messages
        │   ├── processing/     # Leased (being processed)
        │   └── processed/      # Acked (completed)
    """

    def __init__(self, root_path: Path):
        self.root = Path(root_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def _agent_dir(self, agent_id: str) -> Path:
        d = self.root / agent_id
        for sub in ("inbox", "processing", "processed", "quarantine"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def send(self, message: MessageV2) -> str:
        agent_dir = self._agent_dir(message.to_agent)
        # Atomic write: write to .tmp then rename
        tmp_path = agent_dir / "inbox" / f".{message.id}.json.tmp"
        final_path = agent_dir / "inbox" / f"{message.id}.json"
        tmp_path.write_text(message.to_json())
        tmp_path.rename(final_path)
        return message.id

    def poll(self, agent_id: str, limit: int = 10, lease_seconds: int = 60) -> list[MessageV2]:
        agent_dir = self._agent_dir(agent_id)
        inbox = agent_dir / "inbox"
        processing = agent_dir / "processing"

        # Also requeue stale processing items (lease expired)
        self._requeue_stale(agent_id, lease_seconds)

        messages = []
        quarantine = agent_dir / "quarantine"
        files = sorted(inbox.glob("*.json"))[:limit]
        for f in files:
            try:
                raw = f.read_text()
                msg = MessageV2.from_json(raw)
                # Move to processing (atomic claim)
                dest = processing / f.name
                f.rename(dest)
                messages.append(msg)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON in {f.name}, moving to quarantine: {e}")
                try:
                    f.rename(quarantine / f.name)
                except Exception as move_err:
                    logger.error(f"Failed to quarantine {f.name}: {move_err}")
            except Exception as e:
                logger.warning(f"Failed to parse message {f.name}, moving to quarantine: {e}")
                try:
                    f.rename(quarantine / f.name)
                except Exception as move_err:
                    logger.error(f"Failed to quarantine {f.name}: {move_err}")

        return messages

    def ack(self, message_id: str, agent_id: str) -> bool:
        agent_dir = self._agent_dir(agent_id)
        src = agent_dir / "processing" / f"{message_id}.json"
        if src.exists():
            dest = agent_dir / "processed" / f"{message_id}.json"
            src.rename(dest)
            return True
        return False

    def nack(self, message_id: str, agent_id: str, error: Optional[str] = None) -> bool:
        agent_dir = self._agent_dir(agent_id)
        src = agent_dir / "processing" / f"{message_id}.json"
        if src.exists():
            # Move back to inbox for retry
            dest = agent_dir / "inbox" / f"{message_id}.json"
            src.rename(dest)
            return True
        return False

    def get_status(self, message_id: str) -> Optional[dict]:
        # Scan all agent dirs
        for agent_dir in self.root.iterdir():
            if not agent_dir.is_dir():
                continue
            for status_dir in ("inbox", "processing", "processed"):
                path = agent_dir / status_dir / f"{message_id}.json"
                if path.exists():
                    return {
                        "message_id": message_id,
                        "agent": agent_dir.name,
                        "status": status_dir,
                    }
        return None

    def _requeue_stale(self, agent_id: str, lease_seconds: int):
        """Move stale processing items back to inbox."""
        agent_dir = self._agent_dir(agent_id)
        processing = agent_dir / "processing"
        inbox = agent_dir / "inbox"
        cutoff = time.time() - lease_seconds

        for f in processing.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                dest = inbox / f.name
                if not dest.exists():
                    f.rename(dest)
