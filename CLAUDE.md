# agentspool — AI Assistant Context

> Inter-agent communication and coordination for LLM-powered AI entities

## Project Overview

**Type:** Python Library + CLI + MCP Server
**Version:** 0.4.2
**License:** MIT

## Architecture

```
Agent Registry (Minna Memory or local JSON fallback)
         │
    ┌────┼────┐
    │    │    │
  Agent  Agent  Agent N
    │    │    │
    └────┼────┘
         │
  Transport Layer
  ├── SQLiteTransport (primary) — coordination.db with WAL
  ├── HTTPTransport (cross-device) — HMAC-authenticated relay
  └── FileTransport (debug/compat) — JSON inbox files
```

## Key Design Decisions

- **Transport abstraction** — SQLite primary, HTTP for cross-device, extensible via ABC
- **Hybrid routing** — Coordinator routes via local SQLite or remote HTTP based on agent profile
- **HMAC-SHA256 authentication** — Per-agent shared secrets, 5-minute replay protection
- **Lease-based delivery** — Messages are leased (not consumed), must be acked/nacked

## Project Structure

```
agentspool/
├── agent_comm/                   # Core library
│   ├── coordinator.py            # N-agent coordinator (hybrid routing)
│   ├── registry.py               # Agent registry (Minna + local fallback)
│   ├── message_types.py          # Pydantic: MessageV2, PayloadType, Priority
│   ├── spool.py                  # SQLite message spool
│   ├── transports/               # Transport ABC + implementations
│   ├── relay/                    # HTTP relay server + auth
│   └── bridges/                  # OpenClaw gateway bridges
├── agent_comm_mcp/               # MCP server (14 tools)
├── tests/                        # 212 pytest tests
├── scripts/                      # Integration tests (28 tests)
└── docs/                         # Documentation
```

## CLI Quick Reference

```bash
# Register an agent
python3 -m agent_comm register my-agent -c "code,research" -d local

# Send a message
python3 -m agent_comm send sender-agent recipient-agent "Hello" -s "Subject"

# Poll for messages
python3 -m agent_comm poll recipient-agent

# Acknowledge a message
python3 -m agent_comm ack msg_id recipient-agent

# HTTP relay (cross-device)
python3 -m agent_comm relay start --port 8420
python3 -m agent_comm relay gen-secret agent-id
```

## MCP Server

14 tools via FastMCP:

| Tool | Purpose |
|------|---------|
| `comm_send` | Send a message |
| `comm_poll` | Poll for messages |
| `comm_ack` / `comm_nack` | Acknowledge/reject |
| `comm_register_agent` | Register agent |
| `comm_discover_agents` | Find agents |
| `comm_heartbeat` | Keep-alive |
| `comm_deregister` | Remove agent |
| `comm_message_status` | Delivery status |
| `comm_spool_stats` | Queue statistics |
| `comm_get_conversation` | Thread history |
| `comm_cleanup` | Remove expired |
| `comm_relay_gen_secret` | Generate HMAC secret |
| `comm_relay_list_secrets` | List configured secrets |

## Testing

```bash
# All pytest tests (212)
pytest tests/ -v

# Script integration test (28)
python3 scripts/test_two_agents.py

# Total: 240 tests
```

## MessageV2 Protocol

```json
{
  "id": "msg_20260130_143022_a1b2c3",
  "version": "2.0",
  "from": "sender-agent",
  "to": "recipient-agent",
  "subject": "Task completed",
  "body": "Found 3 relevant sources.",
  "timestamp": "2026-01-30T14:30:22Z",
  "priority": "normal",
  "routing": {
    "reply_to": null,
    "conversation_id": "conv_001",
    "ttl_hours": 24
  },
  "payload": {
    "type": "task_result",
    "data": { "task_id": "task_001", "result": "completed" }
  }
}
```

## Dependencies

- `pydantic>=2.0` — Message validation
- `httpx>=0.25.0` — HTTP client for relay
- `mcp[cli]>=1.0.0` — FastMCP server
- `fastapi>=0.104.0` — Relay server (optional)
- `uvicorn>=0.24.0` — ASGI server (optional)
- Python 3.10+

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_COMM_PROJECT_ROOT` | repo root | Project root |
| `AGENT_COMM_DB_PATH` | `./data/coordination.db` | SQLite path |
| `AGENT_COMM_TRANSPORT` | `sqlite` | Default transport |
| `AGENT_COMM_TTL_HOURS` | `24` | Message TTL |
| `AGENT_COMM_LEASE_SECONDS` | `60` | Lease duration |
