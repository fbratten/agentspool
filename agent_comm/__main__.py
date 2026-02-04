"""CLI entry point for agent-comm.

Usage:
    python -m agent_comm register <agent_id> [--capabilities cap1,cap2] [--device dev]
    python -m agent_comm send <from> <to> <body> [--subject subj] [--priority normal]
    python -m agent_comm poll <agent_id> [--limit 10]
    python -m agent_comm ack <message_id> <agent_id>
    python -m agent_comm status <message_id>
    python -m agent_comm agents [--capability cap]
    python -m agent_comm stats
    python -m agent_comm cleanup
    python -m agent_comm bridge <agent_id> --wsl-instance <name> [--poll-interval 5]
    python -m agent_comm bridge <agent_id> --direct [--path-setup "export PATH=..."]
    python -m agent_comm relay start [--host 0.0.0.0] [--port 8420]
    python -m agent_comm relay gen-secret <agent_id>

Bridge modes:
    --wsl-instance  Run from Windows, call into WSL via `wsl -d <instance>`
    --direct        Run inside WSL, call openclaw directly (no WSL wrapper)
"""

import argparse
import json
import sys
from pathlib import Path

from agent_comm.config import AgentCommConfig
from agent_comm.coordinator import Coordinator
from agent_comm.message_types import PayloadType, Priority


def main():
    parser = argparse.ArgumentParser(
        prog="agent-comm",
        description="Inter-agent communication and coordination system",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).parent.parent,
        help="Project root directory",
    )
    parser.add_argument(
        "--transport",
        choices=["sqlite", "file"],
        default="sqlite",
        help="Transport backend (default: sqlite)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # register
    reg = sub.add_parser("register", help="Register an agent")
    reg.add_argument("agent_id", help="Unique agent identifier")
    reg.add_argument("--capabilities", "-c", default="", help="Comma-separated capabilities")
    reg.add_argument("--device", "-d", default="local", help="Device identifier")
    reg.add_argument("--transport-type", "-t", default="sqlite",
                     choices=["sqlite", "file", "http"], help="Transport type for this agent")
    reg.add_argument("--relay-url", default=None, help="Relay URL for HTTP transport agents")
    reg.add_argument("--show-minna", action="store_true", help="Show Minna MCP registration calls")

    # send
    snd = sub.add_parser("send", help="Send a message")
    snd.add_argument("from_agent", help="Sender agent ID")
    snd.add_argument("to_agent", help="Recipient agent ID")
    snd.add_argument("body", help="Message body")
    snd.add_argument("--subject", "-s", default=None)
    snd.add_argument("--priority", "-p", choices=["low", "normal", "high", "urgent"], default="normal")
    snd.add_argument("--reply-to", default=None, help="Message ID being replied to")
    snd.add_argument("--conversation", default=None, help="Conversation ID")
    snd.add_argument("--payload-type", default=None, help="Payload type")
    snd.add_argument("--payload-data", default=None, help="Payload data as JSON string")
    snd.add_argument("--ttl", type=int, default=None, help="TTL in hours")

    # poll
    pol = sub.add_parser("poll", help="Poll for messages")
    pol.add_argument("agent_id", help="Agent ID to poll for")
    pol.add_argument("--limit", "-l", type=int, default=10)
    pol.add_argument("--lease", type=int, default=60, help="Lease duration in seconds")

    # ack
    ak = sub.add_parser("ack", help="Acknowledge a message")
    ak.add_argument("message_id", help="Message ID to ack")
    ak.add_argument("agent_id", help="Agent ID acknowledging")

    # nack
    nak = sub.add_parser("nack", help="Negative-acknowledge a message")
    nak.add_argument("message_id", help="Message ID to nack")
    nak.add_argument("agent_id", help="Agent ID nacking")
    nak.add_argument("--error", default=None, help="Error description")

    # status
    st = sub.add_parser("status", help="Get message delivery status")
    st.add_argument("message_id", help="Message ID")

    # agents
    ag = sub.add_parser("agents", help="List registered agents")
    ag.add_argument("--capability", default=None, help="Filter by capability")

    # stats
    sub.add_parser("stats", help="Show spool statistics")

    # cleanup
    sub.add_parser("cleanup", help="Remove expired messages")

    # bridge
    br = sub.add_parser("bridge", help="Run a bridge to an external agent")
    br.add_argument("agent_id", help="Agent ID to bridge")
    br.add_argument("--direct", action="store_true",
                    help="Direct mode: call openclaw directly (for use inside WSL, no WSL wrapper)")
    br.add_argument("--wsl-instance", "-w", default=None,
                    help="WSL instance name (required for non-direct mode, e.g., MyAgent)")
    br.add_argument("--openclaw-agent", default="main", help="OpenClaw agent name (default: main)")
    br.add_argument("--path-setup", default=None, help="Shell command to set PATH before openclaw")
    br.add_argument("--env", action="append", default=[],
                    help="Environment variable in KEY=VALUE format (can be repeated)")
    br.add_argument("--poll-interval", type=float, default=5.0, help="Poll interval in seconds")
    br.add_argument("--timeout", type=int, default=600, help="Agent response timeout in seconds")
    br.add_argument("--once", action="store_true", help="Run single poll cycle then exit")
    br.add_argument("--check", action="store_true", help="Check if bridge target is available then exit")

    # relay
    relay_cmd = sub.add_parser("relay", help="HTTP relay server commands")
    relay_sub = relay_cmd.add_subparsers(dest="relay_command", required=True)

    relay_start = relay_sub.add_parser("start", help="Start the HTTP relay server")
    relay_start.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    relay_start.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")

    relay_gen = relay_sub.add_parser("gen-secret", help="Generate HMAC secret for an agent")
    relay_gen.add_argument("agent_id", help="Agent ID to generate secret for")

    relay_list = relay_sub.add_parser("list-secrets", help="List agents with configured secrets")

    args = parser.parse_args()

    config = AgentCommConfig(
        project_root=args.project_root,
        default_transport=args.transport,
    )
    coord = Coordinator(config)

    try:
        if args.command == "register":
            caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
            metadata = {}
            if args.relay_url:
                metadata["relay_url"] = args.relay_url
            profile = coord.register_agent(
                agent_id=args.agent_id,
                capabilities=caps,
                transport=args.transport_type,
                device=args.device,
                metadata=metadata,
            )
            print(f"Registered: {profile.agent_id}")
            print(f"  Capabilities: {profile.capabilities}")
            print(f"  Device: {profile.device}")
            print(f"  Transport: {profile.transport}")
            if profile.metadata.get("relay_url"):
                print(f"  Relay URL: {profile.metadata['relay_url']}")
            if args.show_minna:
                calls = coord.get_minna_registration_calls(args.agent_id)
                print("\nMinna MCP calls to execute:")
                for call in calls:
                    print(f"  {call['tool']}({json.dumps(call['args'])})")

        elif args.command == "send":
            payload_type = PayloadType(args.payload_type) if args.payload_type else None
            payload_data = json.loads(args.payload_data) if args.payload_data else None
            msg_id = coord.send(
                from_agent=args.from_agent,
                to_agent=args.to_agent,
                body=args.body,
                subject=args.subject,
                priority=Priority(args.priority),
                reply_to=args.reply_to,
                conversation_id=args.conversation,
                payload_type=payload_type,
                payload_data=payload_data,
                ttl_hours=args.ttl,
            )
            print(f"Sent: {msg_id}")

        elif args.command == "poll":
            messages = coord.poll(args.agent_id, limit=args.limit, lease_seconds=args.lease)
            if not messages:
                print(f"No messages for {args.agent_id}")
            else:
                print(f"Found {len(messages)} message(s) for {args.agent_id}:")
                for msg in messages:
                    print(f"\n  ID: {msg.id}")
                    print(f"  From: {msg.from_agent}")
                    print(f"  Subject: {msg.subject or '(none)'}")
                    print(f"  Body: {msg.body}")
                    if msg.payload:
                        print(f"  Payload: {msg.payload.type.value} = {msg.payload.data}")
                    if msg.routing.conversation_id:
                        print(f"  Conversation: {msg.routing.conversation_id}")

        elif args.command == "ack":
            if coord.ack(args.message_id, args.agent_id):
                print(f"Acked: {args.message_id}")
            else:
                print(f"Failed to ack: {args.message_id}", file=sys.stderr)
                sys.exit(1)

        elif args.command == "nack":
            if coord.nack(args.message_id, args.agent_id, error=args.error):
                print(f"Nacked: {args.message_id}")
            else:
                print(f"Failed to nack: {args.message_id}", file=sys.stderr)
                sys.exit(1)

        elif args.command == "status":
            info = coord.status(args.message_id)
            if info:
                print(json.dumps(info, indent=2, default=str))
            else:
                print(f"No status found for: {args.message_id}")

        elif args.command == "agents":
            agents = coord.discover(capability=args.capability)
            if not agents:
                print("No agents registered")
            else:
                print(f"Registered agents ({len(agents)}):")
                for a in agents:
                    extra = ""
                    if a.transport == "http" and a.metadata.get("relay_url"):
                        extra = f" relay={a.metadata['relay_url']}"
                    print(f"  {a.agent_id} [{a.status}] caps={a.capabilities} "
                          f"device={a.device} transport={a.transport}{extra}")

        elif args.command == "stats":
            from agent_comm.transports.sqlite_transport import SQLiteTransport
            if isinstance(coord.transport, SQLiteTransport):
                stats = coord.transport.spool.stats()
                print(json.dumps(stats, indent=2))
            else:
                print("Stats only available for SQLite transport")

        elif args.command == "cleanup":
            from agent_comm.transports.sqlite_transport import SQLiteTransport
            if isinstance(coord.transport, SQLiteTransport):
                count = coord.transport.spool.cleanup_expired()
                print(f"Cleaned up {count} expired message(s)")
            else:
                print("Cleanup only available for SQLite transport")

        elif args.command == "bridge":
            import logging
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
            from agent_comm.bridges.runner import BridgeRunner

            # Parse environment variables
            env_vars = {}
            for env_str in args.env:
                if "=" in env_str:
                    key, val = env_str.split("=", 1)
                    env_vars[key] = val

            if args.direct:
                # Direct mode: call openclaw directly (for use inside WSL)
                from agent_comm.bridges.openclaw_direct_bridge import (
                    OpenClawDirectBridge, DirectBridgeConfig
                )
                bridge_config = DirectBridgeConfig(
                    agent_id=args.agent_id,
                    openclaw_agent=args.openclaw_agent,
                    path_setup=args.path_setup,
                    env_vars=env_vars,
                    timeout_seconds=args.timeout,
                )
                bridge = OpenClawDirectBridge(bridge_config)
                mode_desc = "direct"
            else:
                # WSL mode: call via wsl -d <instance> (for use from Windows)
                if not args.wsl_instance:
                    print("Error: --wsl-instance is required for non-direct mode", file=sys.stderr)
                    print("  Use --direct for in-WSL mode (no WSL wrapper)", file=sys.stderr)
                    sys.exit(1)
                from agent_comm.bridges.openclaw_bridge import OpenClawBridge, OpenClawConfig
                bridge_config = OpenClawConfig(
                    agent_id=args.agent_id,
                    wsl_instance=args.wsl_instance,
                    openclaw_agent=args.openclaw_agent,
                    path_setup=args.path_setup,
                    timeout_seconds=args.timeout,
                )
                bridge = OpenClawBridge(bridge_config)
                mode_desc = f"WSL:{args.wsl_instance}"

            if args.check:
                available = bridge.is_available()
                status = "available" if available else "unavailable"
                print(f"Bridge target {args.agent_id} ({mode_desc}): {status}")
                sys.exit(0 if available else 1)

            runner = BridgeRunner(
                coordinator=coord,
                bridge=bridge,
                poll_interval=args.poll_interval,
                lease_seconds=args.timeout,
            )

            if args.once:
                count = runner.run_once()
                print(f"Processed {count} message(s)")
            else:
                print(f"Starting bridge for {args.agent_id} via {mode_desc}")
                print(f"  Poll interval: {args.poll_interval}s, Timeout: {args.timeout}s")
                print("  Press Ctrl+C to stop")
                runner.run()

        elif args.command == "relay":
            if args.relay_command == "start":
                import logging
                logging.basicConfig(
                    level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                )
                import uvicorn
                from agent_comm.relay.config import RelayConfig
                from agent_comm.relay.server import create_app

                relay_config = RelayConfig(
                    host=args.host,
                    port=args.port,
                    project_root=args.project_root,
                )
                app = create_app(relay_config)
                print(f"Starting relay server on {args.host}:{args.port}")
                uvicorn.run(app, host=args.host, port=args.port)

            elif args.relay_command == "gen-secret":
                from agent_comm.relay.auth import SecretStore
                store = SecretStore(config.relay_secrets_path)
                hex_secret = store.generate(args.agent_id)
                print(f"Generated secret for {args.agent_id}:")
                print(f"  {hex_secret}")
                print(f"  Saved to: {config.relay_secrets_path}")

            elif args.relay_command == "list-secrets":
                from agent_comm.relay.auth import SecretStore
                store = SecretStore(config.relay_secrets_path)
                agents = store.list_agents()
                if not agents:
                    print("No relay secrets configured")
                else:
                    print(f"Agents with relay secrets ({len(agents)}):")
                    for aid in agents:
                        print(f"  {aid}")

    finally:
        coord.close()


if __name__ == "__main__":
    main()
