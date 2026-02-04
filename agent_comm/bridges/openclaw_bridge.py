"""OpenClaw gateway bridge — forwards messages via `openclaw agent` CLI.

Communicates with OpenClaw/Clawdbot gateway agents running in WSL2 instances.
Uses `wsl -d <instance> -e bash -c 'openclaw agent --agent main --message "..." --json'`
to inject messages into the agent's session and receive responses.

The gateway agent runs a full LLM turn for each message:
- Reads workspace files (MEMORY.md, SOUL.md, etc.)
- Uses tools (exec, browser, gmail, github, etc.)
- Calls the Anthropic API
- Returns JSON with result.payloads[].text
"""

import json
import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent_comm.bridges import Bridge, BridgeResult
from agent_comm.message_types import MessageV2

logger = logging.getLogger(__name__)


@dataclass
class OpenClawConfig:
    """Configuration for an OpenClaw gateway bridge."""

    # The agent ID in the agent-comm registry
    agent_id: str

    # WSL instance name (e.g., "MyAgent")
    wsl_instance: str

    # OpenClaw agent name within the gateway (usually "main")
    openclaw_agent: str = "main"

    # Path setup command run before openclaw (sets PATH, env vars)
    # If None, assumes openclaw is already on PATH in the WSL instance
    path_setup: Optional[str] = None

    # Timeout for the openclaw agent command (seconds)
    timeout_seconds: int = 600

    # Whether to use --json flag for structured output
    json_output: bool = True


class OpenClawBridge(Bridge):
    """Bridge to an OpenClaw/Clawdbot gateway agent running in WSL2.

    Executes `wsl -d <instance> -e bash -c 'openclaw agent --agent main --message "..."'`
    and parses the JSON response.
    """

    def __init__(self, config: OpenClawConfig):
        self.config = config

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    def forward(self, message: MessageV2) -> BridgeResult:
        """Forward a message to the OpenClaw agent via WSL subprocess."""
        # Build the message text to send
        text = self._build_message_text(message)

        # Build the WSL command
        cmd = self._build_command(text)

        logger.info(
            "Forwarding message %s to %s via WSL instance %s",
            message.id, self.config.agent_id, self.config.wsl_instance,
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )

            if result.returncode != 0:
                error = result.stderr.strip() or f"Exit code {result.returncode}"
                logger.error("OpenClaw command failed: %s", error)
                return BridgeResult(
                    success=False,
                    error=error,
                    raw_response=result.stdout,
                )

            # Parse the response
            reply_text = self._parse_response(result.stdout)
            return BridgeResult(
                success=True,
                reply_text=reply_text,
                raw_response=result.stdout,
            )

        except subprocess.TimeoutExpired:
            logger.error(
                "OpenClaw command timed out after %ds for %s",
                self.config.timeout_seconds, self.config.agent_id,
            )
            return BridgeResult(
                success=False,
                error=f"Timeout after {self.config.timeout_seconds}s",
            )
        except FileNotFoundError:
            return BridgeResult(
                success=False,
                error="wsl command not found — is WSL installed?",
            )
        except Exception as e:
            logger.exception("Unexpected error forwarding to %s", self.config.agent_id)
            return BridgeResult(success=False, error=str(e))

    def is_available(self) -> bool:
        """Check if the WSL instance is running."""
        try:
            result = subprocess.run(
                ["wsl", "-d", self.config.wsl_instance, "-e", "echo", "ok"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 and "ok" in result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _build_message_text(self, message: MessageV2) -> str:
        """Build the text to send to the OpenClaw agent.

        Includes subject, body, and payload context if present.
        """
        parts = []
        if message.subject:
            parts.append(f"[Subject: {message.subject}]")
        if message.body:
            parts.append(message.body)
        if message.payload:
            parts.append(f"[Payload type: {message.payload.type.value}]")
            if message.payload.data:
                parts.append(f"[Payload data: {json.dumps(message.payload.data)}]")
        return "\n".join(parts) if parts else "(empty message)"

    def _build_command(self, message_text: str) -> list[str]:
        """Build the WSL subprocess command."""
        # Escape the message for shell embedding
        escaped = shlex.quote(message_text)

        # Build the openclaw command
        openclaw_cmd = f"openclaw agent --agent {self.config.openclaw_agent} --message {escaped}"
        if self.config.json_output:
            openclaw_cmd += " --json"

        # Wrap with path setup if configured
        if self.config.path_setup:
            bash_cmd = f"{self.config.path_setup} && {openclaw_cmd}"
        else:
            bash_cmd = openclaw_cmd

        return ["wsl", "-d", self.config.wsl_instance, "-e", "bash", "-c", bash_cmd]

    def _parse_response(self, stdout: str) -> str:
        """Parse the OpenClaw JSON response, extracting payload text.

        Expected format: {"result": {"payloads": [{"text": "..."}]}}
        Falls back to raw stdout if JSON parsing fails.
        """
        if not self.config.json_output:
            return stdout.strip()

        try:
            data = json.loads(stdout)
            payloads = data.get("result", {}).get("payloads", [])
            texts = [p["text"] for p in payloads if "text" in p]
            if texts:
                return "\n".join(texts)
            # Fallback: try to get any string content
            logger.warning("No text payloads found in OpenClaw response")
            return json.dumps(data, indent=2)
        except json.JSONDecodeError:
            # Not JSON — return raw output (maybe --json wasn't used)
            logger.warning("OpenClaw response is not valid JSON, returning raw output")
            return stdout.strip()
