# agentspool

> Inter-agent communication and coordination for LLM-powered AI entities

## What is this?

A standalone Python library and CLI enabling AI agents, assistants, and bots to communicate and coordinate with each other. Built for the [AdaptiveArts.ai](https://adaptivearts.ai) ecosystem.

**Goal:** N-to-N cross-device agent communication with reliable message delivery.

## Architecture

```
                    Agent Registry (Minna Memory — used as-is)
                    ┌────────────────────────────┐
                    │  Entity: agent:{name}       │
                    │  Attrs: capabilities, state │
                    └──────────┬─────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
    ┌──────┴──────┐    ┌──────┴──────┐    ┌──────┴──────┐
    │  Agent A    │    │  OpenClaw   │    │  Agent N    │
    │  (Device 1) │    │  Gateway    │    │  (any PC)   │
    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
           │                   │                   │
           └───────────────────┼───────────────────┘
                               │
              Transport Layer (SQLite → HTTP → MCP)
```

### Agent Types Supported

| Agent | Runtime | Communication |
|-------|---------|---------------|
| **AI Coding Agents** | Terminal/IDE session | Native Python (coordinator API) |
| **Gateway Agents** | OpenClaw gateway in WSL | CLI: `wsl -d <instance> -e openclaw agent --message "..."` |
| **Custom Agents** | Any Python/TS process | Transport ABC (SQLite, HTTP, or MCP) |

### OpenClaw Gateway Integration (Phase 2)

Gateway agents run as OpenClaw/Clawdbot services inside WSL2 instances. The gateway exposes a direct CLI (`openclaw agent`) that bypasses channel routing (Telegram, Discord, etc.) and talks directly to the agent engine via WebSocket.

```
Calling Agent (Win11 host)
    │
    ├── wsl -d <instance> -e bash -c '...'
    │       └── openclaw agent --agent main --message "..."
    │               └── WebSocket → Gateway (localhost:18789)
    │                       └── Agent Engine (LLM turn)
    │                               ├── Reads workspace files
    │                               ├── Uses tools (exec, browser, gmail, etc.)
    │                               ├── Calls Anthropic API
    │                               └── Returns JSON response
    │
    └── Parses result.payloads[].text
```

**Key properties:**
- No channel needed — bypasses Telegram/WhatsApp/Discord
- Session continuity — agent remembers prior context
- Full agent capabilities — tools, file access, web search, etc.
- Synchronous — blocks until agent completes turn (600s timeout)
- Unidirectional initiation — calling agent initiates; gateway agent responds

## Quick Start

```bash
# Register agents
python3 -m agent_comm register agent-a -c "code,mcp,bash" -d device1
python3 -m agent_comm register agent-b -c "research,chat" -d device2

# Send a message
python3 -m agent_comm send agent-a agent-b "Research AI agent frameworks" \
    -s "Research task" --priority high

# Poll for messages (as recipient)
python3 -m agent_comm poll agent-b

# Acknowledge processing
python3 -m agent_comm ack msg_20260130_143022_a1b2c3 agent-b

# Discover agents by capability
python3 -m agent_comm agents --capability research

# View spool stats
python3 -m agent_comm stats

# Run a bridge to a gateway agent (OpenClaw in WSL)
python3 -m agent_comm bridge agent-b --wsl-instance AgentB

# Check if gateway agent is reachable
python3 -m agent_comm bridge agent-b -w AgentB --check

# Register a remote agent with HTTP transport
python3 -m agent_comm register remote-agent -c "research" -d device2 \
    --transport-type http --relay-url http://100.64.0.2:8420

# Start the relay server (on remote PC)
python3 -m agent_comm relay start --port 8420

# Generate HMAC shared secret for an agent
python3 -m agent_comm relay gen-secret remote-agent
```

## MessageV2 Protocol

```json
{
  "id": "msg_20260130_143022_a1b2c3",
  "version": "2.0",
  "from": "agent-a",
  "to": "agent-b",
  "subject": "Research task",
  "body": "Research AI agent frameworks and summarize findings.",
  "timestamp": "2026-01-30T14:30:22Z",
  "priority": "high",
  "routing": {
    "reply_to": null,
    "conversation_id": "conv_001",
    "ttl_hours": 24
  },
  "payload": {
    "type": "task_assignment",
    "data": { "scope": "broad", "max_sources": 5 }
  }
}
```

### Payload Types

| Type | Purpose |
|------|---------|
| `text` | Free-form message |
| `task_assignment` | Assign work to an agent |
| `task_result` | Report task completion |
| `context_share` | Share knowledge (reference Minna entities) |
| `status_request` / `status_response` | Agent status queries |
| `capability_query` / `capability_response` | Agent capability discovery |
| `broadcast` | Message to all registered agents |

## Transport Layer

| Transport | Mode | Use Case |
|-----------|------|----------|
| `SQLiteTransport` | **Primary** | Local N-agent communication via coordination.db (WAL mode) |
| `HTTPTransport` | **Cross-device** | HMAC-SHA256 authenticated relay (PC-to-PC via Tailscale/LAN) |
| `FileTransport` | Debug/compat | JSON files in inbox directories |

### SQLite Spool Semantics

- **WAL mode** — concurrent read/write
- **Atomic claim** — `UPDATE WHERE status='queued' AND lease_until < NOW()`
- **Idempotency** — `UNIQUE(message_id, recipient_agent_id)`
- **Server-time TTL** — `expires_at` set using DB clock
- **Delivery states:** `queued` → `leased` → `acked` / `failed`
- **Retry:** `attempt`, `max_attempts`, `last_error` per delivery

## Development Phases

| Phase | Status | Description |
|-------|--------|-------------|
| **1. Foundation** | **Complete** | SQLite spool, Transport ABC, registry, CLI |
| **2. Gateway Bridge** | **Complete** | Bridge ABC, OpenClaw bridge, polling runner, CLI `bridge` command |
| **3. HTTP Relay** | **Complete** | FastAPI relay server, HMAC-SHA256 auth, hybrid routing, HTTPTransport |
| **4. MCP Server** | **Complete** | `agent-comm-mcp` — 14 tools via FastMCP (SPINE server #19) |

## Testing

```bash
# All pytest tests (196 tests: 186 unit + 10 relay integration)
.venv/bin/python -m pytest tests/ -v

# Script integration test (28 tests)
python3 scripts/test_two_agents.py

# Everything (224 total)
.venv/bin/python -m pytest tests/ -v && python3 scripts/test_two_agents.py
```

## Dependencies

- Python 3.10+
- `pydantic>=2.0`
- `httpx>=0.25.0`
- `mcp[cli]>=1.0.0`
- `fastapi>=0.104.0` (optional, for relay server)
- `uvicorn>=0.24.0` (optional, for relay server)

## Related Projects

| Project | Relationship |
|---------|-------------|
| [SPINE](https://fbratten.github.io/spine-showcase/) | Multi-agent backbone (agent-comm-mcp is SPINE server #19) |
| [Minna Memory](https://fbratten.github.io/spine-showcase/docs/minna-memory-integration.html) | Agent registry + shared context |
| OpenClaw | Gateway runtime for bot agents |

## Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | Project overview and quick start |
| [docs/CLAUDE_CODE_INTEGRATION.md](docs/CLAUDE_CODE_INTEGRATION.md) | AI agent + OpenClaw integration guide |
| [docs/manual/](docs/manual/) | Full user manual |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

## License

MIT License — See [LICENSE](LICENSE) for details.
