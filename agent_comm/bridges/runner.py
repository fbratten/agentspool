"""Bridge runner — polling daemon that forwards spool messages to external agents.

Runs a continuous loop:
1. Poll spool for messages addressed to the bridged agent
2. Forward each message via the bridge
3. Insert reply into spool
4. Ack the original message

Usage:
    runner = BridgeRunner(coordinator, bridge, poll_interval=5)
    runner.run()  # blocks, Ctrl+C to stop
    # or
    runner.run_once()  # single poll cycle
"""

import logging
import signal
import time
from typing import Optional

from agent_comm.bridges import Bridge, BridgeResult
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import PayloadType

logger = logging.getLogger(__name__)


class BridgeRunner:
    """Polling daemon that bridges spool messages to an external agent."""

    def __init__(
        self,
        coordinator: Coordinator,
        bridge: Bridge,
        poll_interval: float = 5.0,
        lease_seconds: int = 600,
        max_messages_per_poll: int = 1,
    ):
        self.coordinator = coordinator
        self.bridge = bridge
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.max_messages_per_poll = max_messages_per_poll
        self._running = False

    def run(self):
        """Run the polling loop until interrupted (Ctrl+C or SIGTERM)."""
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)

        agent_id = self.bridge.agent_id
        logger.info("Bridge runner started for agent %s (poll every %.1fs)", agent_id, self.poll_interval)

        try:
            while self._running:
                processed = self.run_once()
                if processed == 0:
                    time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Bridge runner interrupted")
        finally:
            self._running = False
            logger.info("Bridge runner stopped for agent %s", agent_id)

    def run_once(self) -> int:
        """Run a single poll cycle. Returns number of messages processed."""
        agent_id = self.bridge.agent_id
        messages = self.coordinator.poll(
            agent_id,
            limit=self.max_messages_per_poll,
            lease_seconds=self.lease_seconds,
        )

        if not messages:
            return 0

        processed = 0
        for message in messages:
            logger.info(
                "Processing message %s from %s → %s",
                message.id, message.from_agent, agent_id,
            )

            result = self.bridge.forward(message)

            if result.success and result.reply_text:
                # Insert reply into spool
                reply_id = self.coordinator.send(
                    from_agent=agent_id,
                    to_agent=message.from_agent,
                    body=result.reply_text,
                    subject=f"Re: {message.subject}" if message.subject else None,
                    reply_to=message.id,
                    conversation_id=message.routing.conversation_id,
                    payload_type=PayloadType.TASK_RESULT,
                    payload_data={"bridge": "openclaw", "original_message_id": message.id},
                )
                logger.info("Reply inserted as %s", reply_id)

                # Ack the original message
                self.coordinator.ack(message.id, agent_id)
                processed += 1

            elif result.success and not result.reply_text:
                logger.warning("Bridge returned success but no reply text for %s", message.id)
                self.coordinator.ack(message.id, agent_id)
                processed += 1

            else:
                logger.error("Bridge failed for %s: %s", message.id, result.error)
                self.coordinator.nack(message.id, agent_id, error=result.error)

        return processed

    def stop(self):
        """Signal the runner to stop after the current cycle."""
        self._running = False

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %d, stopping", signum)
        self.stop()
