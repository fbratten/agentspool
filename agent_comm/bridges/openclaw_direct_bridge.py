"""OpenClaw direct bridge — forwards messages via `openclaw agent` CLI directly.

For use when running INSIDE the same WSL instance as OpenClaw (no WSL wrapper needed).
This is the in-WSL counterpart to OpenClawBridge which requires a Windows host.

Calls `openclaw agent --agent main --message "..." --json` directly as a subprocess.

The gateway agent runs a full LLM turn for each message:
- Reads workspace files (MEMORY.md, SOUL.md, etc.)
- Uses tools (exec, browser, gmail, github, etc.)
- Calls the Anthropic API
- Returns JSON with result.payloads[].text
"""

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent_comm.bridges import Bridge, BridgeResult
from agent_comm.message_types import MessageV2

logger = logging.getLogger(__name__)


@dataclass
class DirectBridgeConfig:
    """Configuration for an OpenClaw direct bridge (in-WSL mode)."""

    # The agent ID in the agent-comm registry
    agent_id: str

    # OpenClaw agent name within the gateway (usually "main")
    openclaw_agent: str = "main"

    # Path setup command run before openclaw (sets PATH, env vars)
    # Example: 'export PATH="$HOME/.local/bin:/home/linuxbrew/.linuxbrew/bin:$PATH"'
    # If None, assumes openclaw is already on PATH
    path_setup: Optional[str] = None

    # Additional environment variables to set
    env_vars: dict = field(default_factory=dict)

    # Timeout for the openclaw agent command (seconds)
    timeout_seconds: int = 600

    # Whether to use --json flag for structured output
    json_output: bool = True

    # Working directory for the openclaw command (None = current dir)
    working_dir: Optional[str] = None


class OpenClawDirectBridge(Bridge):
    """Bridge to an OpenClaw/Clawdbot gateway agent running in the same environment.

    Executes `openclaw agent --agent main --message "..."` directly as a subprocess.
    Use this when running inside WSL alongside OpenClaw (no Windows host required).
    """

    def __init__(self, config: DirectBridgeConfig):
        self.config = config

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    def forward(self, message: MessageV2) -> BridgeResult:
        """Forward a message to the OpenClaw agent via direct subprocess."""
        # Build the message text to send
        text = self._build_message_text(message)

        # Build the command
        cmd = self._build_command(text)

        # Build environment
        env = self._build_env()

        logger.info(
            "Forwarding message %s to %s (direct mode)",
            message.id, self.config.agent_id,
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                env=env,
                cwd=self.config.working_dir,
                shell=True,  # Required for path_setup with && chaining
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
        except FileNotFoundError as e:
            return BridgeResult(
                success=False,
                error=f"Command not found: {e}. Is openclaw installed and on PATH?",
            )
        except Exception as e:
            logger.exception("Unexpected error forwarding to %s", self.config.agent_id)
            return BridgeResult(success=False, error=str(e))

    def is_available(self) -> bool:
        """Check if openclaw command is available."""
        try:
            # Build a simple test command
            if self.config.path_setup:
                cmd = f"{self.config.path_setup} && openclaw --version"
            else:
                cmd = "openclaw --version"

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                shell=True,
                env=self._build_env(),
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
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

    def _build_command(self, message_text: str) -> str:
        """Build the shell command string."""
        # Escape the message for shell embedding
        escaped = shlex.quote(message_text)

        # Build the openclaw command
        openclaw_cmd = f"openclaw agent --agent {self.config.openclaw_agent} --message {escaped}"
        if self.config.json_output:
            openclaw_cmd += " --json"

        # Wrap with path setup if configured
        if self.config.path_setup:
            return f"{self.config.path_setup} && {openclaw_cmd}"
        return openclaw_cmd

    def _build_env(self) -> dict:
        """Build the environment dict for subprocess."""
        env = os.environ.copy()
        env.update(self.config.env_vars)
        return env

    def _parse_response(self, stdout: str) -> str:
        """Parse the OpenClaw JSON response, extracting payload text.

        Expected format: {"result": {"payloads": [{"text": "..."}]}}
        Falls back to raw stdout if JSON parsing fails.
        """
        if not self.config.json_output:
            return stdout.strip()

        try:
            data = json.loads(stdout)
            # Try nested format first (result.payloads), then top-level
            payloads = data.get("result", {}).get("payloads", [])
            if not payloads:
                payloads = data.get("payloads", [])
            texts = [p["text"] for p in payloads if "text" in p and p["text"]]
            if texts:
                return "\n".join(texts)
            # Fallback: try to get any string content
            logger.warning("No text payloads found in OpenClaw response")
            return json.dumps(data, indent=2)
        except json.JSONDecodeError:
            # Not JSON — return raw output (maybe --json wasn't used)
            logger.warning("OpenClaw response is not valid JSON, returning raw output")
            return stdout.strip()
