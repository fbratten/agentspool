# Claude Code ↔ OpenClaw Agent Integration Guide

**Purpose:** Enable bidirectional messaging between Claude Code (running on Windows) and OpenClaw/Clawdbot gateway agents (running in WSL2)

**Version:** 1.0.0
**Last Updated:** 2026-02-03
**Status:** Production Ready (Phase 4 Complete, 224 tests: 196 pytest + 28 script integration)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Agent Registration](#agent-registration)
6. [Message Flow](#message-flow)
7. [OpenClaw Bridge Integration](#openclaw-bridge-integration)
8. [Claude Code Integration Patterns](#claude-code-integration-patterns)
9. [End-to-End Walkthrough](#end-to-end-walkthrough)
10. [Troubleshooting](#troubleshooting)
11. [Current Limitations](#current-limitations)
12. [Future Enhancements](#future-enhancements)
13. [Reference](#reference)

---

## Overview

### The Problem

Claude Code and OpenClaw gateway agents operate in different contexts:

| Component | Runtime | Location |
|-----------|---------|----------|
| Claude Code | Windows terminal session | Windows 11 host |
| OpenClaw Agent | Gateway service | WSL2 Linux instance |

By default, communication is **one-way only**:

```
Claude Code → OpenClaw Agent ✅  (via `wsl -d <instance> -e openclaw agent --message "..."`)
OpenClaw Agent → Claude Code ❌  (no mechanism)
```

### The Solution

**agent-comm** provides a SQLite-backed message spool with:
- Atomic claim/lease semantics for reliable delivery
- Agent registry with capability discovery
- CLI tools for send/poll/ack workflows
- OpenClaw bridge for gateway integration
- MCP server with 14 tools for programmatic access

This enables **true bidirectional communication**:

```
Claude Code ←→ Message Spool ←→ OpenClaw Agent
     ↓              ↓              ↓
   send()      coordination.db   poll()
   poll()                        send()
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Windows 11 Host                          │
│                                                                 │
│  ┌─────────────────┐                                           │
│  │   Claude Code   │                                           │
│  │   (Terminal)    │                                           │
│  └────────┬────────┘                                           │
│           │                                                     │
│           │ wsl -d <instance> -e bash -c '...'                 │
│           ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    WSL2 Instance                         │   │
│  │                                                          │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌───────────┐  │   │
│  │  │ agent-comm   │    │ Message      │    │ OpenClaw  │  │   │
│  │  │ CLI/Library  │◄──►│ Spool        │◄──►│ Gateway   │  │   │
│  │  └──────────────┘    │ (SQLite)     │    │ Agent     │  │   │
│  │                      └──────────────┘    └───────────┘  │   │
│  │                                                          │   │
│  │  Location: ~/projects/agent-comm/data/coordination.db   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Purpose | Location |
|-----------|---------|----------|
| `agent_comm` | Core Python library | `~/projects/agent-comm/agent_comm/` |
| `coordination.db` | SQLite message spool | `~/projects/agent-comm/data/` |
| `agent_comm_mcp` | MCP server (14 tools) | `~/projects/agent-comm/agent_comm_mcp/` |
| CLI | Command-line interface | `python -m agent_comm <command>` |

---

## Prerequisites

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Windows | Windows 10 21H2 | Windows 11 |
| WSL2 | Any distro | Ubuntu 24.04 |
| Python | 3.10+ | 3.12 |
| OpenClaw/Clawdbot | 2026.1.x | Latest |

### Software Dependencies

**On WSL2 instance:**
- Python 3.10+ with pip
- uv (recommended) or pip for package management
- Git for cloning the repository
- OpenClaw or Clawdbot gateway installed and running

**On Windows host:**
- Claude Code CLI installed
- WSL2 configured with target instance

### Required Access

- GitHub access to `fbratten/agentspool` repository
- Personal Access Token with `repo` read scope

---

## Installation

### Step 1: Clone the Repository

```bash
# From Windows, execute in WSL
wsl -d <your-instance> -e bash -c '
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/fbratten/agentspool.git
'
```

**If using a Personal Access Token:**

```bash
wsl -d <your-instance> -e bash -c '
git config --global credential.helper store
echo "https://<username>:<PAT>@github.com" >> ~/.git-credentials
cd ~/projects
git clone https://github.com/fbratten/agentspool.git
'
```

### Step 2: Create Virtual Environment

```bash
wsl -d <your-instance> -e bash -c '
cd ~/projects/agent-comm
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
'
```

**Using uv (faster):**

```bash
wsl -d <your-instance> -e bash -c '
cd ~/projects/agent-comm
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
'
```

### Step 3: Verify Installation

```bash
wsl -d <your-instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm --help
'
```

**Expected output:**

```
usage: agent-comm [-h] [--project-root PROJECT_ROOT]
                  [--transport {sqlite,file}]
                  {register,send,poll,ack,nack,status,agents,stats,cleanup,bridge,relay} ...

Inter-agent communication and coordination system
```

---

## Agent Registration

Before agents can communicate, they must be registered in the agent-comm registry.

### Register the OpenClaw Agent

```bash
wsl -d <your-instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm register <agent-name> \
  -c "research,chat,gmail,calendar,tools,files" \
  -d <device-id>
'
```

**Parameters:**
- `<agent-name>`: Unique identifier (e.g., `gateway-agent`, `assistant-1`)
- `-c`: Comma-separated capabilities
- `-d`: Device identifier for routing

**Example:**

```bash
.venv/bin/python -m agent_comm register my-assistant \
  -c "research,chat,gmail,tools" \
  -d wsl-main
```

### Register Claude Code

```bash
wsl -d <your-instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm register claude-code \
  -c "code,implementation,bash,mcp" \
  -d windows-host
'
```

### Verify Registration

```bash
wsl -d <your-instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm agents
'
```

**Expected output:**

```
Registered agents (2):
  my-assistant [active] caps=['research', 'chat', 'gmail', 'tools'] device=wsl-main transport=sqlite
  claude-code [active] caps=['code', 'implementation', 'bash', 'mcp'] device=windows-host transport=sqlite
```

---

## Message Flow

### MessageV2 Protocol

All messages follow the MessageV2 schema:

```json
{
  "id": "msg_20260203_143022_a1b2c3",
  "version": "2.0",
  "from": "claude-code",
  "to": "my-assistant",
  "subject": "Implementation task",
  "body": "Please implement the user authentication module.",
  "timestamp": "2026-02-03T14:30:22Z",
  "priority": "high",
  "routing": {
    "reply_to": null,
    "conversation_id": "conv_001",
    "ttl_hours": 24
  },
  "payload": {
    "type": "task_assignment",
    "data": { "scope": "feature", "files": ["auth.py", "login.py"] }
  }
}
```

### Delivery States

```
queued → leased → acked
                ↘ failed (after max_attempts)
```

| State | Description |
|-------|-------------|
| `queued` | Message waiting for recipient |
| `leased` | Claimed by recipient, processing |
| `acked` | Successfully processed |
| `failed` | Delivery failed after retries |

### CLI Commands

#### Send a Message

```bash
.venv/bin/python -m agent_comm send <from> <to> "<body>" \
  -s "<subject>" \
  --priority <normal|high|low>
```

**Example:**

```bash
.venv/bin/python -m agent_comm send claude-code my-assistant \
  "Please research AI agent frameworks and summarize findings" \
  -s "Research task" \
  --priority high
```

**Output:**

```
Sent: msg_20260203_143022_a1b2c3
```

#### Poll for Messages

```bash
.venv/bin/python -m agent_comm poll <agent-name>
```

**Example:**

```bash
.venv/bin/python -m agent_comm poll my-assistant
```

**Output:**

```
Found 1 message(s) for my-assistant:

  ID: msg_20260203_143022_a1b2c3
  From: claude-code
  Subject: Research task
  Body: Please research AI agent frameworks and summarize findings
```

#### Acknowledge a Message

```bash
.venv/bin/python -m agent_comm ack <message-id> <agent-name>
```

**Example:**

```bash
.venv/bin/python -m agent_comm ack msg_20260203_143022_a1b2c3 my-assistant
```

#### View Spool Statistics

```bash
.venv/bin/python -m agent_comm stats
```

**Output:**

```json
{
  "total_messages": 5,
  "deliveries": {
    "queued": 1,
    "acked": 4
  }
}
```

---

## OpenClaw Bridge Integration

The OpenClaw bridge connects agent-comm to OpenClaw/Clawdbot gateway agents.

### How the Bridge Works

```
1. Claude Code sends message → Spool (queued)
2. Bridge polls spool for messages to gateway agent
3. Bridge calls: openclaw agent --message "<body>" --json
4. Gateway agent processes and responds
5. Bridge sends response back → Spool
6. Claude Code polls for response
```

### Bridge Command

```bash
.venv/bin/python -m agent_comm bridge <agent-name> --wsl-instance <instance>
```

**Parameters:**
- `<agent-name>`: The registered agent to bridge
- `--wsl-instance`: WSL2 instance name where OpenClaw runs

### Current Limitation

> **Important:** The automated bridge expects to run from Windows host and call `wsl -d <instance>`. When running the bridge from *inside* WSL, it fails because `wsl` command is not available in that context.

**Current workaround:** Manually forward messages using `openclaw agent --message`.

### Manual Message Forwarding

When the automated bridge cannot be used, forward messages manually:

```bash
# 1. Poll for pending messages
.venv/bin/python -m agent_comm poll <gateway-agent>

# 2. Forward to OpenClaw
export PATH="/home/<user>/.local/share/pnpm:/home/linuxbrew/.linuxbrew/bin:$PATH"
openclaw agent --agent main --message "<message-body>" --json

# 3. Parse response and send reply
.venv/bin/python -m agent_comm send <gateway-agent> <sender> "<response>" -s "Re: <subject>"

# 4. Ack the original message
.venv/bin/python -m agent_comm ack <message-id> <gateway-agent>
```

---

## Claude Code Integration Patterns

### Pattern 1: Direct CLI (Simplest)

Execute agent-comm commands directly from Claude Code via bash:

```bash
# Send to gateway agent
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm send claude-code <agent> "Your message" -s "Subject"
'

# Poll for responses
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm poll claude-code
'
```

### Pattern 2: Shell Wrapper Script

Create a wrapper script for convenience:

**File: `~/projects/agent-comm/scripts/cc-send.sh`**

```bash
#!/bin/bash
# Usage: ./cc-send.sh <to-agent> "<message>" "<subject>"
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm send claude-code "$1" "$2" -s "$3"
```

**File: `~/projects/agent-comm/scripts/cc-poll.sh`**

```bash
#!/bin/bash
# Usage: ./cc-poll.sh
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm poll claude-code
```

### Pattern 3: Claude Code Skill

Create a Claude Code skill for integrated messaging:

**File: `.claude/skills/agent-comm/SKILL.md`**

```yaml
---
name: agent-comm
description: Send messages to and poll from registered agents via agent-comm
allowed-tools:
  - Bash
---

# Agent Communication Skill

Use agent-comm to communicate with registered agents.

## Send a Message

```bash
wsl -d <instance> -e bash -c 'cd ~/projects/agent-comm && .venv/bin/python -m agent_comm send claude-code <agent> "$ARGUMENTS" -s "From Claude Code"'
```

## Poll for Responses

```bash
wsl -d <instance> -e bash -c 'cd ~/projects/agent-comm && .venv/bin/python -m agent_comm poll claude-code'
```
```

### Pattern 4: MCP Server Integration

agent-comm includes an MCP server with 14 tools:

| Tool | Purpose |
|------|---------|
| `comm_register_agent` | Register a new agent |
| `comm_send` | Send a message |
| `comm_poll` | Poll for pending messages |
| `comm_ack` | Acknowledge a message |
| `comm_nack` | Negative acknowledge |
| `comm_message_status` | Check delivery status |
| `comm_discover_agents` | List registered agents |
| `comm_deregister` | Remove an agent |
| `comm_spool_stats` | Spool statistics |
| `comm_cleanup` | Remove expired messages |
| `comm_get_conversation` | Get conversation history |
| `comm_heartbeat` | Update agent heartbeat |
| `comm_relay_gen_secret` | Generate relay HMAC secret |
| `comm_relay_list_secrets` | List relay secrets |

**MCP Configuration (`.mcp.json`):**

```json
{
  "mcpServers": {
    "agent-comm-mcp": {
      "command": "python",
      "args": ["-m", "agent_comm_mcp"],
      "cwd": "/path/to/agent-comm",
      "env": {
        "PYTHONPATH": "/path/to/agent-comm"
      }
    }
  }
}
```

---

## End-to-End Walkthrough

This walkthrough demonstrates complete bidirectional communication.

### Setup

```bash
# Verify agents are registered
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm agents
'
```

### Step 1: Claude Code Sends Task

```bash
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm send claude-code my-assistant \
  "Please analyze the authentication module and suggest improvements" \
  -s "Code review request" \
  --priority high
'
```

**Output:**
```
Sent: msg_20260203_150000_abc123
```

### Step 2: Gateway Agent Polls and Processes

```bash
# Poll for messages
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm poll my-assistant
'

# Forward to OpenClaw (manual bridge)
wsl -d <instance> -e bash -c '
export PATH="$HOME/.local/share/pnpm:/home/linuxbrew/.linuxbrew/bin:$PATH"
openclaw agent --agent main --message "[agent-comm msg_20260203_150000_abc123] Please analyze the authentication module and suggest improvements" --json
'
```

### Step 3: Gateway Agent Sends Response

```bash
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm send my-assistant claude-code \
  "Analysis complete. Found 3 issues: 1) Missing rate limiting, 2) Weak password hashing, 3) No session timeout. Recommendations attached." \
  -s "Re: Code review request"
'
```

### Step 4: Claude Code Receives Response

```bash
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm poll claude-code
'
```

**Output:**
```
Found 1 message(s) for claude-code:

  ID: msg_20260203_150130_def456
  From: my-assistant
  Subject: Re: Code review request
  Body: Analysis complete. Found 3 issues: 1) Missing rate limiting...
```

### Step 5: Acknowledge Messages

```bash
wsl -d <instance> -e bash -c '
cd ~/projects/agent-comm
.venv/bin/python -m agent_comm ack msg_20260203_150000_abc123 my-assistant
.venv/bin/python -m agent_comm ack msg_20260203_150130_def456 claude-code
'
```

---

## Troubleshooting

### Common Issues

#### 1. "Command not found: agent_comm"

**Cause:** Virtual environment not activated or not in PATH.

**Fix:**
```bash
cd ~/projects/agent-comm
source .venv/bin/activate
python -m agent_comm --help
```

Or use full path:
```bash
~/projects/agent-comm/.venv/bin/python -m agent_comm --help
```

#### 2. "No agents registered"

**Cause:** Agents not registered in the spool.

**Fix:**
```bash
.venv/bin/python -m agent_comm register <agent-name> -c "capability1,capability2" -d <device>
```

#### 3. "wsl command not found" (Bridge Error)

**Cause:** Running the bridge from inside WSL instead of Windows.

**Current workaround:** Use manual message forwarding (see [OpenClaw Bridge Integration](#openclaw-bridge-integration)).

#### 4. "Permission denied" accessing coordination.db

**Cause:** Database locked by another process or permission issue.

**Fix:**
```bash
# Check for locks
lsof ~/projects/agent-comm/data/coordination.db

# Reset permissions
chmod 664 ~/projects/agent-comm/data/coordination.db
```

#### 5. Message stuck in "leased" state

**Cause:** Agent crashed during processing without acking.

**Fix:**
```bash
# Nack to return to queue
.venv/bin/python -m agent_comm nack <message-id> <agent>

# Or cleanup expired leases
.venv/bin/python -m agent_comm cleanup
```

### Diagnostic Commands

```bash
# View all messages and their states
.venv/bin/python -m agent_comm stats

# Check specific message status
.venv/bin/python -m agent_comm status <message-id>

# List all registered agents
.venv/bin/python -m agent_comm agents

# View SQLite database directly
sqlite3 ~/projects/agent-comm/data/coordination.db ".schema"
```

---

## Current Limitations

### 1. Bridge Requires Windows Host

The automated bridge (`agent_comm bridge`) is designed to run from Windows and call into WSL via `wsl -d <instance>`. This means:

- Cannot run the bridge daemon from inside WSL
- Messages require manual forwarding when bridge unavailable
- No fully automated polling loop inside WSL

**Workaround:** Manual message forwarding using `openclaw agent --message`.

### 2. No Push Notifications

Currently uses polling model:
- Agents must actively poll for messages
- No webhook/callback mechanism for instant delivery
- Polling interval determines latency

### 3. Single-Device SQLite

The SQLite transport (`coordination.db`) is local to one device:
- Cross-device requires HTTP relay transport
- Relay server must be running on target device
- Tailscale/LAN required for connectivity

---

## Available: In-WSL Bridge (Direct Mode)

The direct bridge runs inside WSL and calls `openclaw agent` directly — no Windows host or WSL wrapper required.

### Usage

```bash
# Check if bridge target is available
python -m agent_comm bridge nelly --direct \
  --path-setup 'export PATH="$HOME/.local/share/pnpm:/home/linuxbrew/.linuxbrew/bin:$PATH"' \
  --check

# Run bridge daemon (polls and forwards messages)
python -m agent_comm bridge nelly --direct \
  --path-setup 'export PATH="$HOME/.local/share/pnpm:/home/linuxbrew/.linuxbrew/bin:$PATH"' \
  --env GOG_KEYRING_PASSWORD=your-keyring-password \
  --poll-interval 5

# Single poll cycle
python -m agent_comm bridge nelly --direct --path-setup '...' --once
```

### Python API

```python
from agent_comm.bridges import OpenClawDirectBridge
from agent_comm.bridges.openclaw_direct_bridge import DirectBridgeConfig

config = DirectBridgeConfig(
    agent_id="nelly",
    openclaw_agent="main",
    path_setup='export PATH="$HOME/.local/share/pnpm:/home/linuxbrew/.linuxbrew/bin:$PATH"',
    env_vars={"GOG_KEYRING_PASSWORD": "your-keyring-password"},
    timeout_seconds=600,
)
bridge = OpenClawDirectBridge(config)

# Check availability
if bridge.is_available():
    result = bridge.forward(message)
```

### Benefits
- Eliminates Windows dependency for bridge
- Can run as systemd user service inside WSL
- Enables fully automated message delivery

---

## Future Enhancements

### 1. Webhook Integration

Add webhook support for real-time delivery:
- Gateway calls webhook URL when message arrives
- Claude Code hosts lightweight webhook receiver
- Eliminates polling latency

### 3. Systemd Service

Package bridge as systemd user service:

```ini
[Unit]
Description=agent-comm bridge for OpenClaw
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/user/projects/agent-comm
ExecStart=/home/user/projects/agent-comm/.venv/bin/python -m agent_comm bridge my-assistant --direct
Restart=always

[Install]
WantedBy=default.target
```

---

## Reference

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `AGENT_COMM_DB_PATH` | SQLite database path | `./data/coordination.db` |
| `AGENT_COMM_PROJECT_ROOT` | Project root for registry/config | agent-comm repo root |
| `AGENT_COMM_TRANSPORT` | Transport backend (sqlite\|file) | `sqlite` |
| `AGENT_COMM_TTL_HOURS` | Default message TTL in hours | `24` |
| `AGENT_COMM_LEASE_SECONDS` | Default lease duration in seconds | `60` |

### CLI Quick Reference

| Command | Description |
|---------|-------------|
| `register <name>` | Register an agent |
| `send <from> <to> <body>` | Send a message |
| `poll <agent>` | Poll for messages |
| `ack <msg-id> <agent>` | Acknowledge message |
| `nack <msg-id> <agent>` | Negative acknowledge |
| `status <msg-id>` | Check delivery status |
| `agents` | List registered agents |
| `stats` | View spool statistics |
| `cleanup` | Remove expired messages |
| `bridge <agent>` | Run bridge daemon |
| `relay start` | Start HTTP relay server |

### Project Files

| Path | Purpose |
|------|---------|
| `~/projects/agent-comm/` | Project root |
| `agent_comm/` | Core library |
| `agent_comm_mcp/` | MCP server |
| `data/coordination.db` | Message spool |
| `.venv/` | Python virtual environment |
| `tests/` | Test suite (224 tests) |

### Related Documentation

- [README.md](README.md) — Project overview and quick start
- [docs/manual/](docs/manual/) — Full user manual
- [CHANGELOG.md](CHANGELOG.md) — Version history
- [CLAUDE.md](CLAUDE.md) — Claude Code project guidelines

---

## Human Setup Guide (Quick Reference)

For humans setting up Claude Code to communicate with an OpenClaw agent:

### 1. Clone & Install

```bash
wsl -d <your-wsl-instance>
cd ~/projects
git clone https://github.com/fbratten/agentspool.git
cd agent-comm
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Register Agents

```bash
python -m agent_comm register my-gateway-agent -c "chat,tools" -d wsl
python -m agent_comm register claude-code -c "code,bash" -d windows
```

### 3. Test Communication

```bash
# From Claude Code (via WSL)
python -m agent_comm send claude-code my-gateway-agent "Hello!" -s "Test"

# Check delivery
python -m agent_comm poll my-gateway-agent
```

### 4. Forward to OpenClaw

```bash
# Manual forwarding (until automated bridge supports in-WSL mode)
openclaw agent --message "Hello!" --json
```

That's it! Bidirectional messaging is now enabled.

---

*Document created: 2026-02-03*
*Part of the agent-comm project — Inter-agent communication for LLM-powered AI entities*
