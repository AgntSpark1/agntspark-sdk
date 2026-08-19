#!/usr/bin/env python
"""
Example: Monitor an agent's metrics and logs.

This script polls agent metrics and fetches recent log entries on a loop,
which is useful for debugging or keeping an eye on production agents.

Run::

    AGNTSPARK_API_KEY=sk-… python examples/monitor_agent.py agt_abc123
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from agntspark import AgentStatus, Client
from agntspark.exceptions import AgntSparkError


def main(agent_id: str, interval: int = 10) -> None:
    with Client() as client:
        print(f"Monitoring agent {agent_id} (polling every {interval}s)…")
        print("Press Ctrl+C to stop.\n")

        while True:
            try:
                # Fetch current state
                agent = client.agents.get(agent_id)
                metrics = client.agents.metrics(agent_id, window="5m")
                logs = client.agents.logs(agent_id, limit=5)

                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] Status: {agent.status.value} | Replicas: {agent.replicas}")
                print(
                    f"  CPU: {metrics.cpu_percent:.1f}%  "
                    f"Mem: {metrics.memory_mb}MB  "
                    f"Req: {metrics.request_count} "
                    f"({metrics.request_rate:.1f}/s)  "
                    f"Errors: {metrics.error_count}"
                )
                print(f"  P50: {metrics.p50_latency_ms:.0f}ms  "
                      f"P95: {metrics.p95_latency_ms:.0f}ms  "
                      f"P99: {metrics.p99_latency_ms:.0f}ms")

                if logs:
                    print("  Recent logs:")
                    for log in logs[-3:]:
                        print(f"    [{log.level}] {log.message}")

                if agent.status in (AgentStatus.FAILED, AgentStatus.CRASHED):
                    print(f"\n⚠ Agent is in {agent.status.value} state: {agent.error}")
                    break

                print()
                time.sleep(interval)

            except AgntSparkError as e:
                print(f"Error: {e}")
                time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python monitor_agent.py <agent_id> [interval_seconds]")
        sys.exit(1)
    agent_id = sys.argv[1]
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    try:
        main(agent_id, interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
