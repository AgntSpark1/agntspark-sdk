#!/usr/bin/env python
"""
Example: Deploy multiple agents in parallel using asyncio.

This script demonstrates the async API by deploying several agents
concurrently — which is much faster than sequential deployment.

Run::

    AGNTSPARK_API_KEY=sk-… python examples/multi_deploy.py
"""

from __future__ import annotations

import asyncio
import sys

from agntspark import Client, DeployConfig, ResourceLimits
from agntspark.exceptions import AgntSparkError


# Configuration for each agent we want to deploy
AGENT_CONFIGS = [
    {
        "name": "research-agent",
        "framework": "crewai",
        "model": "claude-3-opus",
        "system_prompt": "You are a research assistant that finds and summarises academic papers.",
        "replicas": 1,
    },
    {
        "name": "code-review-agent",
        "framework": "autogen",
        "model": "gpt-4o",
        "system_prompt": "You are a senior code reviewer. Analyse pull requests and provide feedback.",
        "replicas": 2,
    },
    {
        "name": "data-extraction-agent",
        "framework": "langchain",
        "model": "gpt-4o-mini",
        "system_prompt": "Extract structured data from unstructured text inputs.",
        "replicas": 3,
    },
    {
        "name": "summarisation-agent",
        "framework": "custom",
        "model": "llama-3-70b",
        "system_prompt": "Summarise long documents into concise bullet points.",
        "replicas": 2,
    },
]


async def deploy_one(client: Client, config: dict) -> str:
    """Deploy a single agent and return its ID."""
    name = config["name"]
    try:
        deploy_config = DeployConfig(
            replicas=config["replicas"],
            resources=ResourceLimits(cpu=1.0, memory_mb=1024),
            health_check_path="/health",
        )

        agent = await client.agents.create_async(
            name=name,
            framework=config["framework"],
            model=config["model"],
            system_prompt=config["system_prompt"],
            deploy=deploy_config,
            tags=["batch-deploy"],
        )

        print(f"  ✓ {name}: created → {agent.id}")

        deployed = await client.agents.deploy_async(agent.id, config=deploy_config)
        print(f"  ✓ {name}: deployed → status={deployed.status.value}")
        return agent.id

    except AgntSparkError as e:
        print(f"  ✗ {name}: failed — {e}")
        return ""


async def main() -> None:
    print(f"Deploying {len(AGENT_CONFIGS)} agents in parallel…\n")

    async with Client() as client:
        # Deploy all agents concurrently
        tasks = [deploy_one(client, cfg) for cfg in AGENT_CONFIGS]
        results = await asyncio.gather(*tasks)

    # Summary
    successful = [r for r in results if r]
    failed = len(results) - len(successful)

    print(f"\n{'='*50}")
    print(f"Deployment complete: {len(successful)} succeeded, {failed} failed")
    for i, cfg in enumerate(AGENT_CONFIGS):
        status = "✓" if results[i] else "✗"
        print(f"  {status} {cfg['name']}: {results[i] or 'FAILED'}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDeployment cancelled.")
        sys.exit(1)
