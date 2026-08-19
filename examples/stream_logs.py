#!/usr/bin/env python
"""
Example: Stream agent logs in real time via SSE.

This script uses the async streaming interface to tail agent logs
as they happen — no polling required.

Run::

    AGNTSPARK_API_KEY=sk-… python examples/stream_logs.py agt_abc123
"""

from __future__ import annotations

import asyncio
import sys

from agntspark import Client, EventType


async def stream(agent_id: str) -> None:
    """Stream logs from an agent in real time."""
    async with Client() as client:
        print(f"Streaming logs for {agent_id}… (Ctrl+C to stop)\n")

        async for event in client.agents.stream_logs(agent_id):
            if event.type == EventType.LOG:
                data = event.data
                ts = data.get("timestamp", "")
                level = data.get("level", "INFO")
                replica = data.get("replica_id", "?")[:8]
                message = data.get("message", "")
                print(f"[{ts}] [{level:5s}] [{replica}] {message}")

            elif event.type == EventType.METRIC:
                data = event.data
                print(
                    f"  [metric] CPU: {data.get('cpu_percent', 0):.1f}%  "
                    f"Mem: {data.get('memory_mb', 0)}MB"
                )

            elif event.type == EventType.HEARTBEAT:
                # SSE keepalive — skip silently
                pass

            elif event.type == EventType.ERROR:
                print(f"[ERROR] {event.data}", file=sys.stderr)

            elif event.type == EventType.STATUS:
                data = event.data
                print(f"  [status] {data.get('status', 'unknown')}")

            elif event.type == EventType.CONNECTED:
                print("  [connected] SSE stream established")

            elif event.type == EventType.DISCONNECTED:
                print("  [disconnected] SSE stream lost, attempting reconnect…")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python stream_logs.py <agent_id>")
        sys.exit(1)
    agent_id = sys.argv[1]

    try:
        asyncio.run(stream(agent_id))
    except KeyboardInterrupt:
        print("\nStream stopped.")


if __name__ == "__main__":
    main()
