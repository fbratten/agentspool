# Changelog

All notable changes to agent-comm will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.4.2] - 2026-02-04

### Security

- **Relay auth binding** — Endpoints `/api/v1/ack`, `/nack`, `/agents/register` now use authenticated identity from HMAC headers instead of JSON body. Prevents cross-agent impersonation.
- **Coordinator sender identity** — HTTP transport cache key changed from `relay_url` to `(relay_url, sender_id)` tuple. Each sender now uses its own HMAC secret for signing.

### Fixed

- **Atomic registry writes** — `LocalRegistry._save()` now uses `tempfile.mkstemp()` + `os.replace()` for atomic writes, preventing corruption from concurrent access or crashes mid-write.
- **MCP enum validation** — `SendMessageInput` now uses Pydantic enum types (`Priority`, `PayloadType`) instead of strings with manual casts. Invalid values now produce clear Pydantic validation errors.
- **comm_get_conversation output** — Returns parsed `MessageV2` objects matching `comm_poll` format instead of raw DB rows with JSON strings.
- **File transport logging** — `poll()` now logs warnings for malformed files and moves them to a `quarantine/` subdirectory instead of silently skipping.

### Documentation

- **Dual SQLite clarification** — Added comment in `relay/server.py` explaining that `MessageSpool` and `SQLiteTransport` sharing the same DB file is intentional and safe under WAL mode.

---

## [0.4.1] - 2026-02-01

### Fixed

- **Priority ordering in poll()** — Replaced boolean ORDER BY (`priority = 'urgent' DESC, priority = 'high' DESC`) with CASE expression that correctly distinguishes all 4 levels: urgent > high > normal > low. Previously normal and low were treated identically, falling to FIFO only.

### Added

- **MCP test suite hardening** — 38 new behavioral tests in 8 test classes, validating messaging semantics through the MCP layer:
  - `TestConversationThreading` (6) — conversation_id/reply_to flow, isolation, multi-turn chains
  - `TestLeaseExpiryAndRequeue` (5) — expired lease reclaim, attempt increment, acked immutability
  - `TestNackRetrySemantics` (5) — requeue, error preservation, 3-nack exhaustion → failed
  - `TestTTLEnforcementAndCleanup` (5) — expired message filtering, cleanup cascade to deliveries
  - `TestPriorityOrdering` (4) — urgent/high/normal/low ordering, same-priority FIFO
  - `TestMessageStatusLifecycle` (4) — queued → leased → acked/failed transitions
  - `TestErrorHandlingAndEdgeCases` (6) — wrong agent ack/nack, double ack, all 9 payload types, deregister isolation
  - `TestSpoolStatsAccuracy` (3) — stats reflect leased/mixed/post-cleanup states
- DB manipulation helpers for time-sensitive tests (no `time.sleep()`)
- **End-to-end HTTP relay integration test** (`tests/test_relay_integration.py`) — 10 tests against a real uvicorn server (no mocks):
  - Module-scoped fixture: starts uvicorn on random localhost port, registers agent secrets
  - `test_health_check`, `test_send_via_http`, `test_poll_via_http`, `test_ack_via_http`, `test_nack_via_http`
  - `test_full_round_trip`, `test_conversation_threading_via_http`, `test_priority_ordering_via_http`
  - `test_multiple_agents_isolation`, `test_auth_rejection`
- **Total: 196 pytest tests + 28 script integration = 224 tests**

---

## [0.4.0] - 2026-01-31

### Added

- **MCP server** (`agent_comm_mcp/`) — Exposes agent-comm as 14 MCP tools via FastMCP for Claude Code and other AI agents
  - `agent_comm_mcp/server.py` — FastMCP server with lazy Coordinator singleton, 11 Pydantic input models, env var configuration
  - **Messaging tools:** `comm_send`, `comm_poll`, `comm_ack`, `comm_nack`
  - **Registry tools:** `comm_register_agent`, `comm_discover_agents`, `comm_heartbeat`, `comm_deregister`
  - **Status tools:** `comm_message_status`, `comm_spool_stats`, `comm_get_conversation`
  - **Maintenance tools:** `comm_cleanup`
  - **Relay admin tools:** `comm_relay_gen_secret`, `comm_relay_list_secrets`
  - MCP tool annotations: `readOnlyHint`, `destructiveHint`, `idempotentHint` per tool
- **`pyproject.toml`** — Hatchling build system packaging both `agent_comm` and `agent_comm_mcp` in one wheel. Entry point: `agent-comm-mcp`
- **MCP server tests** (`tests/test_mcp_server.py`) — 30 tests covering all 14 tools via direct function calls

### Changed

- Dependencies: added `mcp[cli]>=1.0.0` (FastMCP framework)
- `.mcp.json`: added `agent-comm-mcp` server entry

---

## [0.3.0] - 2026-01-31

### Added

- **HTTP relay server** (`relay/`) — FastAPI-based relay for cross-device agent communication
  - `relay/auth.py` — `SecretStore` (per-agent shared secrets in JSON), `HMACSigner` (HMAC-SHA256 sign/verify with constant-time comparison), `NonceTracker` (in-memory replay protection with 5-min window)
  - `relay/config.py` — `RelayConfig` dataclass (host, port, db_path, secrets_path, registry_path, replay_window)
  - `relay/server.py` — FastAPI app with endpoints: `POST /api/v1/send`, `GET /api/v1/poll/{agent_id}`, `POST /api/v1/ack`, `POST /api/v1/nack`, `GET /api/v1/status/{message_id}`, `GET /api/v1/health`, `POST /api/v1/agents/register`. All endpoints (except health) authenticated with HMAC-SHA256.
- **HTTPTransport** (`transports/http_transport.py`) — Client-side transport implementing Transport ABC. Uses `httpx.Client` (sync). Signs requests with HMAC-SHA256 (body-based for POST, action-based for GET). Auth headers: `X-Agent-ID`, `X-Timestamp`, `X-Nonce`, `X-Signature`.
- **Hybrid routing in Coordinator** — `send()` now looks up `AgentProfile.transport` field. `"sqlite"` routes to local spool, `"http"` routes to HTTPTransport pointed at `profile.metadata["relay_url"]`. HTTPTransport instances cached by URL.
- **CLI relay commands** — `relay start` (uvicorn server), `relay gen-secret` (generate per-agent HMAC secret), `relay list-secrets` (list configured agents). `register` command gains `--transport-type` and `--relay-url` flags.
- **Relay tests** — 56 new tests across 4 files:
  - `test_relay_auth.py` (22) — SecretStore, HMACSigner, NonceTracker
  - `test_http_transport.py` (11) — HTTPTransport with mocked httpx
  - `test_relay_server.py` (14) — FastAPI TestClient endpoint tests
  - `test_coordinator_routing.py` (9) — Hybrid routing logic

### Changed

- `MessageSpool` and `SQLiteTransport` now accept `check_same_thread` parameter for use in async/threaded servers
- Dependencies: added `httpx>=0.25.0`, `fastapi>=0.104.0`, `uvicorn>=0.24.0`

---

## [0.2.0] - 2026-01-31

### Added

- **Bridge abstraction** (`bridges/`) — `Bridge` ABC with `forward()`, `is_available()`, and `BridgeResult` dataclass
- **OpenClaw bridge** (`bridges/openclaw_bridge.py`) — Forwards messages to OpenClaw/Clawdbot gateway agents via `wsl -d <instance> -e openclaw agent --message "..." --json`. Parses `result.payloads[].text` from JSON response. Handles timeouts, nonzero exits, WSL not found.
- **Bridge runner** (`bridges/runner.py`) — Polling daemon: poll spool → forward via bridge → insert reply → ack original. Supports continuous mode (Ctrl+C to stop) and single-cycle mode (`--once`).
- **CLI `bridge` command** — `python3 -m agent_comm bridge <agent_id> --wsl-instance <name>`. Options: `--poll-interval`, `--timeout`, `--once`, `--check`, `--path-setup`, `--openclaw-agent`.
- **Bridge tests** (`tests/test_bridge.py`) — 20 tests: OpenClaw bridge (message building, command building, response parsing, subprocess mocking for success/error/timeout/missing-wsl, availability check) + BridgeRunner (no messages, forward+ack, nack on failure, multiple messages, stop).

---

## [0.1.1] - 2026-01-30

### Fixed

- **Priority ordering in poll()** — The final `SELECT * FROM messages WHERE id IN (...)` returned rows in arbitrary order, losing the priority sort from the delivery query. Fixed by re-ordering fetched messages to match the priority-sorted `message_ids` list.

### Added

- **Unit tests** (42 tests across 3 files):
  - `tests/test_spool.py` — 20 tests: insert, poll, lease, ack, nack, TTL expiry, priority ordering, conversation threading, cleanup, stats
  - `tests/test_registry.py` — 13 tests: register, discover, heartbeat, persistence, deregister, Minna call generation
  - `tests/test_coordinator.py` — 9 tests: send/poll, payload, threading, ack, nack, priority, discovery, Minna calls
- **Minna Memory agents** — Registered `agent:claude-code-pc1` and `agent:nelly-pc2` with capabilities, transport, device, and runtime info
- **GitHub repo** — Pushed to https://github.com/fbratten/agentspool

---

## [0.1.0] - 2026-01-30

### Added

- **MessageV2 protocol** (`message_types.py`) — Pydantic models with typed payloads, priority levels, conversation threading, TTL, and version negotiation
- **SQLite message spool** (`spool.py`) — WAL-mode durable queue with atomic claim, lease/ack semantics, idempotency via unique constraints, server-time TTL expiry
- **Transport abstraction** (`transports/`) — `Transport` ABC with `send()`, `poll()`, `ack()`, `nack()`, `get_status()` methods
- **SQLiteTransport** — primary transport backed by coordination.db
- **FileTransport** — debug/compatibility transport using JSON inbox files with atomic rename
- **Agent registry** (`registry.py`) — local JSON-backed registry with Minna Memory MCP call generation
- **Coordinator** (`coordinator.py`) — core N-agent coordinator tying registry + transport + message protocol
- **CLI** (`__main__.py`) — full command-line interface: `register`, `send`, `poll`, `ack`, `nack`, `status`, `agents`, `stats`, `cleanup`
- **Integration test** (`scripts/test_two_agents.py`) — 28 tests covering registration, send/poll, lease semantics, ack, conversation threading, idempotency, agent discovery, Minna call generation
- **Project scaffolding** — CLAUDE.md, NEXT.md, README.md, CHANGELOG.md, `.mcp.json`, `.spine/config.json`

### Architecture Decisions

- **ADR: SQLite spool as primary transport** — SQLite WAL over file-based inboxes. File transport kept as debug mode. Based on analysis of race conditions and atomicity issues with file-based queues across Windows/WSL.
- **ADR: Minna Memory as-is** — Minna used via MCP tools for agent registry and shared context. Source code (`mem-system-lite-mcp`) is never modified. Minna handles semantic memory; `coordination.db` handles mechanical queue semantics.
- **ADR: Standalone project** — Not embedded in SPINE, agent-coordination, or Minna. Own `.mcp.json`, `.spine/config.json`, independent lifecycle.
- **ADR: Transport ABC from day one** — SQLite (MVP), HTTP (scale), MCP (ecosystem). Same MessageV2 protocol, pluggable pipes.

### Known Integration Target

- **OpenClaw/Clawdbot gateway** — Nelly and bot agents run as gateway services in WSL2 instances. Communication via `openclaw agent --agent main --message "..." --json` CLI, bypassing channel routing. WebSocket gateway on `localhost:18789`. Unidirectional initiation (Claude Code → agent). This is the primary target for Phase 2.
