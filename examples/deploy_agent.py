#!/usr/bin/env python
"""
Example: Deploy an agent from a configuration dictionary.

This script demonstrates the full lifecycle:
  1. Create the SDK client
  2. Define an agent configuration
  3. Create the agent on the platform
  4. Deploy it with resource limits
  5. Verify deployment status

Run::

    AGNTSPARK_API_KEY=sk-… python examples/deploy_agent.py
"""

from __future__ import annotations

import sys

from agntspark import Client, DeployConfig, ResourceLimits


def main() -> None:
    # 1. Initialise the client (reads AGNTSPARK_API_KEY from env)
    with Client() as client:
        # 2. Define deployment configuration
        deploy_config = DeployConfig(
            replicas=2,
            resources=ResourceLimits(
                cpu=2.0,
                memory_mb=2048,
                gpu=1,
                gpu_type="nvidia-a10g",
            ),
            health_check_path="/health",
            auto_scale=True,
            min_replicas=2,
            max_replicas=10,
            port=8080,
        )

        # 3. Create the agent
        print("Creating agent…")
        agent = client.agents.create(
            name="support-bot-prod",
            framework="langchain",
            model="gpt-4o",
            system_prompt="You are a helpful customer support assistant.",
            deploy=deploy_config,
            tags=["production", "support"],
            metadata={"team": "cs", "cost_center": "ops-123"},
        )
        print(f"  ✓ Agent created: {agent.id}")
        print(f"    Status: {agent.status.value}")

        # 4. Deploy (create() with deploy= already deploys, but we can redeploy)
        if agent.status.value == "pending":
            print("Deploying agent…")
            agent = client.agents.deploy(agent.id)
            print(f"  ✓ Deployed. Status: {agent.status.value}")

        # 5. Print final state
        print(f"\nAgent URL: {agent.url or '(not yet available)'}")
        print(f"Replicas: {agent.replicas}")
        print(f"Version: {agent.version}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
