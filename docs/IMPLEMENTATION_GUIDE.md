# agent-comm Implementation Guide

**Purpose:** Enable bidirectional messaging between AI agents, assistants, and LLM-powered entities

**Version:** 1.0.0
**Last Updated:** 2026-02-03
**Status:** Production Ready (Phase 4 Complete, 224 tests: 196 pytest + 28 script integration)

---

## Table of Contents

1. [Introduction](#introduction)
2. [Core Concepts](#core-concepts)
3. [Architecture Overview](#architecture-overview)
4. [Getting Started](#getting-started)
5. [Agent Registration](#agent-registration)
6. [Sending Messages](#sending-messages)
7. [Receiving Messages](#receiving-messages)
8. [Message Acknowledgment](#message-acknowledgment)
9. [Conversation Threading](#conversation-threading)
10. [Bridge Architecture](#bridge-architecture)
11. [MCP Server Integration](#mcp-server-integration)
12. [Deployment Patterns](#deployment-patterns)
13. [Security Considerations](#security-considerations)
14. [Monitoring and Observability](#monitoring-and-observability)
15. [Troubleshooting](#troubleshooting)
16. [API Reference](#api-reference)
17. [Best Practices](#best-practices)

---

## Introduction

### What is agent-comm?

**agent-comm** is a standalone Python library and CLI that enables AI agents to communicate and coordinate with each other. It provides:

- **Message Spool:** SQLite-backed queue with atomic delivery semantics
- **Agent Registry:** Capability-based agent discovery
- **Transport Abstraction:** SQLite (local), HTTP (cross-device), File (debug)
- **Bridge Framework:** Connect to external agent runtimes
- **MCP Server:** 14 tools for programmatic access

### Use Cases

| Scenario | Description |
|----------|-------------|
| **Task Delegation** | Architect agent dispatches implementation tasks to builder agents |
| **Research Coordination** | Multiple agents collaborate on research, sharing findings |
| **Human-in-the-Loop** | Agent requests human approval before proceeding |
| **Cross-Device Agents** | Agents on different machines communicate via HTTP relay |
| **Hybrid Workflows** | Mix of AI agents and human operators in same message flow |

### Design Philosophy

1. **Transport Agnostic:** Same API whether messages travel via SQLite, HTTP, or files
2. **Reliable Delivery:** Atomic claim/lease/ack semantics prevent message loss
3. **Agent Autonomy:** Each agent polls independently; no central coordinator required
4. **Extensible Bridges:** Connect any agent runtime via the Bridge ABC
5. **Zero External Dependencies:** Core functionality needs only Python + SQLite

---

## Core Concepts

### Agents

An **agent** is any entity that can send and receive messages:

```python
class AgentProfile(BaseModel):
    agent_id: str                # Unique identifier
    capabilities: list[str]      # What this agent can do
    device: str                  # Device/location identifier
    transport: str               # How to reach this agent (sqlite, file, http)
    status: str                  # active, inactive, offline
    last_seen: datetime          # Last activity timestamp
    metadata: dict               # Custom attributes
```

**Example agents:**

| Agent | Capabilities | Transport |
|-------|--------------|-----------|
| `claude-code` | code, bash, implementation | sqlite |
| `research-bot` | research, summarize, cite | sqlite |
| `approval-queue` | human-review, approve, reject | http |

### Messages

Messages follow the **MessageV2** protocol:

```python
@dataclass
class MessageV2:
    id: str                      # Unique message ID
    version: str                 # Protocol version (2.0)
    from_agent: str              # Sender agent name
    to_agent: str                # Recipient agent name
    subject: str                 # Brief description
    body: str                    # Full message content
    timestamp: datetime          # When sent
    priority: Priority           # critical, high, normal, low
    routing: RoutingInfo         # Reply-to, conversation ID, TTL
    payload: Payload             # Typed payload (task, result, etc.)
```

### Delivery States

```
           ┌─────────┐
           │ queued  │ ← Message sent, waiting for recipient
           └────┬────┘
                │
         poll() │ claim
                ▼
           ┌─────────┐
           │ leased  │ ← Claimed by recipient, processing
           └────┬────┘
                │
       ┌────────┴────────┐
       │                 │
   ack()              nack() / timeout
       │                 │
       ▼                 ▼
  ┌─────────┐      ┌─────────┐
  │  acked  │      │ queued  │ ← Retry (up to max_attempts)
  └─────────┘      └────┬────┘
                        │
                   max retries
                        │
                        ▼
                   ┌─────────┐
                   │ failed  │
                   └─────────┘
```

### Payload Types

| Type | Purpose | Example |
|------|---------|---------|
| `text` | Free-form message | General communication |
| `task_assignment` | Assign work | "Implement feature X" |
| `task_result` | Report completion | "Feature X complete, PR #123" |
| `context_share` | Share knowledge | "Here's what I found about Y" |
| `status_request` | Query agent status | "Are you available?" |
| `status_response` | Report status | "Busy, 3 tasks queued" |
| `capability_query` | Discover capabilities | "What can you do?" |
| `capability_response` | Report capabilities | "I can: research, summarize" |
| `broadcast` | Message to all agents | Announcements |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Agent Communication Layer                   │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Agent A  │  │ Agent B  │  │ Agent C  │  │ Agent N  │        │
│  │ (Claude) │  │ (Bot)    │  │ (Human)  │  │ (Any)    │        │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
│       │             │             │             │               │
│       └─────────────┴──────┬──────┴─────────────┘               │
│                            │                                     │
│                    ┌───────┴───────┐                            │
│                    │  Coordinator  │                            │
│                    │   (Hybrid     │                            │
│                    │   Routing)    │                            │
│                    └───────┬───────┘                            │
│                            │                                     │
│       ┌────────────────────┼────────────────────┐               │
│       │                    │                    │               │
│  ┌────┴─────┐        ┌────┴─────┐        ┌────┴─────┐         │
│  │ SQLite   │        │  HTTP    │        │  File    │         │
│  │Transport │        │Transport │        │Transport │         │
│  │ (local)  │        │ (remote) │        │ (debug)  │         │
│  └────┬─────┘        └────┬─────┘        └────┬─────┘         │
│       │                   │                   │                │
│       ▼                   ▼                   ▼                │
│  coordination.db    HTTP Relay         ./inbox/*.json         │
│                     Server                                     │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| **Coordinator** | Routes messages to correct transport based on agent profile |
| **SQLiteTransport** | Local message spool with WAL mode for concurrent access |
| **HTTPTransport** | Cross-device messaging via authenticated relay server |
| **FileTransport** | Debug/testing transport using JSON files |
| **Registry** | Agent registration, capability discovery, status tracking |
| **Bridge** | Connects external agent runtimes to the message spool |

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- pip or uv package manager
- Git (for cloning)

### Installation

```bash
# Clone the repository
git clone https://github.com/fbratten/agentspool.git
cd agentspool

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows

# Install package
pip install -e ".[dev]"
```

### Verify Installation

```bash
python -m agent_comm --help
```

**Expected output:**

```
usage: agent-comm [-h] [--project-root PROJECT_ROOT]
                  [--transport {sqlite,file}]
                  {register,send,poll,ack,nack,status,agents,stats,cleanup,bridge,relay} ...

Inter-agent communication and coordination system
```

### Quick Test

```bash
# Register two agents
python -m agent_comm register agent-a -c "send,receive" -d local
python -m agent_comm register agent-b -c "send,receive" -d local

# Send a message
python -m agent_comm send agent-a agent-b "Hello from A!" -s "Greeting"

# Poll as recipient
python -m agent_comm poll agent-b

# Acknowledge
python -m agent_comm ack <message-id> agent-b
```

---

## Agent Registration

### Register an Agent

```bash
python -m agent_comm register <name> \
  -c "<capability1>,<capability2>,..." \
  -d <device-id> \
  [--transport-type <sqlite|http>] \
  [--relay-url <url>]
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `name` | Yes | Unique agent identifier |
| `-c, --capabilities` | Yes | Comma-separated capability list |
| `-d, --device` | Yes | Device/location identifier |
| `--transport-type` | No | Transport backend (default: sqlite) |
| `--relay-url` | No | HTTP relay URL (if transport=http) |

### Capability Guidelines

Choose capabilities that describe what the agent can do:

| Category | Example Capabilities |
|----------|---------------------|
| **Development** | code, review, test, deploy, debug |
| **Research** | research, summarize, cite, analyze |
| **Communication** | email, chat, notify, broadcast |
| **Data** | query, transform, visualize, export |
| **Workflow** | approve, schedule, prioritize, delegate |

### List Registered Agents

```bash
python -m agent_comm agents
```

**Output:**

```
Registered agents (3):
  agent-a [active] caps=['code', 'review'] device=workstation transport=sqlite
  agent-b [active] caps=['research', 'summarize'] device=server transport=sqlite
  agent-c [active] caps=['approve'] device=remote transport=http
```

### Discover by Capability

```bash
python -m agent_comm agents --capability research
```

---

## Sending Messages

### Basic Send

```bash
python -m agent_comm send <from> <to> "<body>" -s "<subject>"
```

**Example:**

```bash
python -m agent_comm send architect builder \
  "Please implement user authentication with JWT tokens. Requirements: ..." \
  -s "Task: Auth Module"
```

### Priority Levels

```bash
python -m agent_comm send <from> <to> "<body>" -s "<subject>" --priority <level>
```

| Priority | Use Case |
|----------|----------|
| `critical` | System failures, security issues |
| `high` | Urgent tasks, blocking issues |
| `normal` | Standard tasks (default) |
| `low` | Background tasks, nice-to-have |

### Programmatic Send (Python)

```python
from agent_comm import Coordinator, MessageV2, Priority

coord = Coordinator()

message = MessageV2(
    from_agent="architect",
    to_agent="builder",
    subject="Task: Auth Module",
    body="Please implement user authentication...",
    priority=Priority.HIGH,
)

msg_id = coord.send(message)
print(f"Sent: {msg_id}")
```

---

## Receiving Messages

### Poll for Messages

```bash
python -m agent_comm poll <agent-name>
```

**Output:**

```
Found 2 message(s) for builder:

  ID: msg_20260203_143022_a1b2c3
  From: architect
  Subject: Task: Auth Module
  Body: Please implement user authentication...

  ID: msg_20260203_143155_d4e5f6
  From: reviewer
  Subject: Code Review Request
  Body: Please review PR #123...
```

### Programmatic Poll (Python)

```python
from agent_comm import Coordinator

coord = Coordinator()

messages = coord.poll("builder")
for msg in messages:
    print(f"From: {msg.from_agent}")
    print(f"Subject: {msg.subject}")
    print(f"Body: {msg.body}")

    # Process message...

    coord.ack(msg.id, "builder")
```

### Polling Strategies

| Strategy | Implementation | Use Case |
|----------|---------------|----------|
| **On-demand** | Poll when needed | Interactive agents |
| **Periodic** | Poll every N seconds | Background workers |
| **Event-driven** | Poll on trigger | Webhook-activated |
| **Continuous** | Poll in loop | Dedicated message processor |

**Continuous polling example:**

```python
import time
from agent_comm import Coordinator

coord = Coordinator()

while True:
    messages = coord.poll("my-agent")
    for msg in messages:
        process(msg)
        coord.ack(msg.id, "my-agent")
    time.sleep(30)  # Poll every 30 seconds
```

---

## Message Acknowledgment

### Acknowledge (Success)

```bash
python -m agent_comm ack <message-id> <agent-name>
```

**When to ack:**
- Message successfully processed
- Task completed
- Response sent

### Negative Acknowledge (Failure)

```bash
python -m agent_comm nack <message-id> <agent-name> --reason "Error details..."
```

**When to nack:**
- Processing failed but retry may succeed
- Temporary error (network, resource unavailable)
- Want message requeued for retry

### Ack vs Nack vs Ignore

| Action | Effect | Use When |
|--------|--------|----------|
| `ack` | Message marked complete | Successfully processed |
| `nack` | Message requeued for retry | Temporary failure |
| *ignore* | Lease expires, auto-requeue | Agent crashed |

### Lease Timeout

Messages have a lease timeout (default: 5 minutes). If not acked within the lease period, the message automatically returns to `queued` state for redelivery.

---

## Conversation Threading

### Reply to a Message

```python
from agent_comm import Coordinator, MessageV2

coord = Coordinator()

# Original message
original = coord.poll("builder")[0]

# Reply
reply = MessageV2(
    from_agent="builder",
    to_agent=original.from_agent,
    subject=f"Re: {original.subject}",
    body="Task complete. PR #456 ready for review.",
    routing=RoutingInfo(
        reply_to=original.id,
        conversation_id=original.routing.conversation_id,
    ),
)

coord.send(reply)
coord.ack(original.id, "builder")
```

### Conversation ID

All messages in a conversation share the same `conversation_id`. This enables:

- Grouping related messages
- Tracking task progress
- Auditing workflows

```bash
# List conversations
python -m agent_comm conversations

# Search within conversation
python -m agent_comm search --conversation conv_20260203_abc123
```

---

## Bridge Architecture

### What is a Bridge?

A **bridge** connects external agent runtimes to the agent-comm message spool. It:

1. Polls the spool for messages to a specific agent
2. Forwards messages to the external runtime
3. Captures responses and sends them back

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│  Message Spool  │ ──────► │     Bridge      │ ──────► │ External Agent  │
│                 │ ◄────── │                 │ ◄────── │    Runtime      │
└─────────────────┘         └─────────────────┘         └─────────────────┘
      poll()                   forward()                    process()
      send()                   capture()                    respond()
```

### Bridge Interface

```python
from abc import ABC, abstractmethod
from agent_comm import MessageV2, BridgeResult

class Bridge(ABC):
    @abstractmethod
    async def forward(self, message: MessageV2) -> BridgeResult:
        """Forward message to external runtime and return response."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if external runtime is reachable."""
        pass
```

### Implementing a Custom Bridge

```python
from agent_comm.bridges import Bridge, BridgeResult
from agent_comm import MessageV2
import subprocess

class MyAgentBridge(Bridge):
    def __init__(self, agent_command: str):
        self.command = agent_command

    async def forward(self, message: MessageV2) -> BridgeResult:
        # Call external agent
        result = subprocess.run(
            [self.command, "--message", message.body],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return BridgeResult(
                success=True,
                reply_text=result.stdout,
            )
        else:
            return BridgeResult(
                success=False,
                error=result.stderr,
            )

    def is_available(self) -> bool:
        result = subprocess.run([self.command, "--health"], capture_output=True)
        return result.returncode == 0
```

### Running a Bridge

```bash
python -m agent_comm bridge <agent-name> [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--once` | Process one batch and exit |
| `--interval` | Polling interval in seconds |
| `--max-messages` | Max messages per batch |

### Current Limitations

> **Note:** The built-in bridges expect specific runtime environments. For Windows/WSL setups, the bridge expects to run from the Windows host and call into WSL. For fully automated delivery within WSL, a custom in-process bridge is recommended.

**Future enhancement:** An in-process bridge that calls agent runtimes directly without shell wrappers.

---

## MCP Server Integration

### Overview

agent-comm includes an MCP (Model Context Protocol) server with 14 tools:

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

### Configuration

Add to your MCP configuration (`.mcp.json` or equivalent):

```json
{
  "mcpServers": {
    "agent-comm": {
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

### Using MCP Tools

Once configured, AI assistants can use agent-comm tools directly:

```
User: Send a message to the research agent asking about AI frameworks
Assistant: [Uses send_message tool with to="research-agent", body="What are the best AI agent frameworks in 2026?"]
```

---

## Deployment Patterns

### Pattern 1: Single Machine (Development)

All agents on one machine using SQLite transport:

```
┌──────────────────────────────────────┐
│           Single Machine             │
│                                      │
│  Agent A ◄──► coordination.db ◄──► Agent B
│                                      │
└──────────────────────────────────────┘
```

**Setup:**
```bash
python -m agent_comm register agent-a -c "..." -d local
python -m agent_comm register agent-b -c "..." -d local
```

### Pattern 2: Cross-Device (Production)

Agents on different machines using HTTP relay:

```
┌─────────────┐          ┌─────────────┐
│   Machine A │          │   Machine B │
│             │          │             │
│   Agent A   │◄──HTTP──►│   Agent B   │
│      │      │          │      │      │
│      ▼      │          │      ▼      │
│  local.db   │          │  relay.db   │
└─────────────┘          └─────────────┘
```

**Setup:**

On Machine B (relay server):
```bash
python -m agent_comm relay start --port 8420
python -m agent_comm relay gen-secret agent-b
```

On Machine A (client):
```bash
python -m agent_comm register agent-b \
  --transport-type http \
  --relay-url http://machine-b:8420
```

### Pattern 3: Hybrid (Recommended)

Local agents use SQLite; remote agents use HTTP:

```bash
# Local agent
python -m agent_comm register local-agent -c "..." -d local

# Remote agent
python -m agent_comm register remote-agent \
  -c "..." \
  -d remote \
  --transport-type http \
  --relay-url http://remote-host:8420
```

The coordinator automatically routes based on transport type.

---

## Security Considerations

### Authentication

HTTP transport uses HMAC-SHA256 authentication:

```python
# Message signing
signature = hmac_sha256(shared_secret, message_payload + timestamp + nonce)
```

**Security properties:**
- Shared secret per agent pair
- Timestamp prevents replay (5-minute window)
- Nonce prevents duplicate delivery

### Secret Management

```bash
# Generate secret for an agent
python -m agent_comm relay gen-secret <agent-name>

# Store secret securely (environment variable recommended)
export AGENT_COMM_SECRET_<agent-name>="..."
```

### Network Security

| Recommendation | Description |
|----------------|-------------|
| Use TLS | Always run relay behind HTTPS proxy |
| Firewall | Restrict relay port to known IPs |
| VPN/Tailscale | Use private network for cross-device |
| Rotate secrets | Regenerate secrets periodically |

### Data Protection

- SQLite databases contain message content
- Use filesystem permissions to restrict access
- Consider encryption at rest for sensitive deployments

---

## Monitoring and Observability

### Spool Statistics

```bash
python -m agent_comm stats
```

**Output:**
```json
{
  "total_messages": 150,
  "deliveries": {
    "queued": 5,
    "leased": 2,
    "acked": 140,
    "failed": 3
  },
  "agents": {
    "active": 4,
    "inactive": 1
  }
}
```

### Key Metrics

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `queued_count` | Messages waiting | > 100 |
| `leased_age_max` | Oldest leased message | > 10 minutes |
| `failed_rate` | Failed / total | > 5% |
| `agent_inactive` | Agents not polling | > 1 hour |

### Logging

Enable verbose logging:
```bash
export AGENT_COMM_LOG_LEVEL=DEBUG
python -m agent_comm poll my-agent
```

### Health Checks

```bash
# Check agent reachability (for bridged agents)
python -m agent_comm bridge <agent> --check

# Verify database integrity
python -m agent_comm stats --verify
```

---

## Troubleshooting

### Common Issues

#### "Agent not found"

```
Error: Agent 'my-agent' not registered
```

**Fix:** Register the agent first:
```bash
python -m agent_comm register my-agent -c "capabilities" -d device
```

#### "Database locked"

```
Error: database is locked
```

**Fix:** Another process has the database open. Check for:
```bash
lsof data/coordination.db
```

#### "Message stuck in leased"

**Cause:** Agent claimed message but crashed before acking.

**Fix:**
```bash
# Wait for lease to expire (5 minutes), or:
python -m agent_comm nack <msg-id> <agent> --reason "Manual reset"
```

#### "HTTP relay connection refused"

**Fix:**
1. Check relay server is running: `python -m agent_comm relay start`
2. Check firewall allows port
3. Verify URL: `--relay-url http://host:port`

#### "HMAC verification failed"

**Cause:** Shared secret mismatch or clock skew.

**Fix:**
1. Regenerate secret: `python -m agent_comm relay gen-secret <agent>`
2. Sync clocks: `sudo ntpdate pool.ntp.org`

### Diagnostic Commands

```bash
# List all agents
python -m agent_comm agents

# Check message status
python -m agent_comm status <message-id>

# View spool contents
sqlite3 data/coordination.db "SELECT id, from_agent, to_agent, status FROM messages"

# Cleanup expired
python -m agent_comm cleanup --dry-run
```

---

## API Reference

### CLI Commands

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
| `relay gen-secret <agent>` | Generate HMAC secret |

### Python API

```python
from agent_comm import (
    Coordinator,
    MessageV2,
    Priority,
    PayloadType,
    RoutingInfo,
)

# Initialize
coord = Coordinator(project_root="/path/to/project")

# Send
msg_id = coord.send(message)

# Poll
messages = coord.poll(agent_name)

# Ack/Nack
coord.ack(msg_id, agent_name)
coord.nack(msg_id, agent_name, reason="...")

# Registry
coord.registry.register(agent_profile)
coord.registry.get(agent_name)
coord.registry.list()
coord.registry.find_by_capability(capability)
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENT_COMM_DB_PATH` | SQLite database path | `./data/coordination.db` |
| `AGENT_COMM_PROJECT_ROOT` | Project root for registry/config | agent-comm repo root |
| `AGENT_COMM_TRANSPORT` | Transport backend (sqlite\|file) | `sqlite` |
| `AGENT_COMM_TTL_HOURS` | Default message TTL in hours | `24` |
| `AGENT_COMM_LEASE_SECONDS` | Default lease duration in seconds | `60` |

---

## Best Practices

### Agent Design

1. **Single Responsibility:** Each agent should have a clear, focused purpose
2. **Capability Accuracy:** Only advertise capabilities the agent actually has
3. **Graceful Degradation:** Handle unavailable agents gracefully
4. **Idempotent Processing:** Design handlers to be safe for retry

### Message Design

1. **Clear Subjects:** Make subjects searchable and descriptive
2. **Structured Bodies:** Use consistent formats (JSON, Markdown)
3. **Appropriate Priority:** Reserve `critical` for true emergencies
4. **Reasonable TTL:** Set TTL based on message importance

### Reliability

1. **Always Ack:** Never leave messages unacknowledged
2. **Handle Failures:** Use nack for retryable errors
3. **Monitor Queues:** Alert on growing backlogs
4. **Test Bridges:** Verify external connectivity regularly

### Performance

1. **Batch When Possible:** Process multiple messages per poll
2. **Tune Intervals:** Balance responsiveness vs resource usage
3. **Cleanup Regularly:** Remove expired messages to keep DB small
4. **Index Appropriately:** SQLite indexes on agent_id, status

---

## Appendix: Quick Start Checklist

- [ ] Clone repository
- [ ] Create virtual environment
- [ ] Install package
- [ ] Register agents
- [ ] Test send/poll/ack cycle
- [ ] Configure bridges (if needed)
- [ ] Set up monitoring
- [ ] Document agent capabilities

---

*Document created: 2026-02-03*
*Part of the agent-comm project — Inter-agent communication for LLM-powered AI entities*
