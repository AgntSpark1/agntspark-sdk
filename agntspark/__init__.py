"""
AgntSpark Python SDK
====================

The official Python SDK for the AgntSpark AI Agent hosting platform.

Basic usage::

    from agntspark import Client

    with Client(api_key="sk-…") as client:
        agent = client.agents.create(name="my-bot", model="gpt-4o")
        client.agents.deploy(agent.id)

Async usage::

    import asyncio
    from agntspark import Client

    async def main():
        async with Client(api_key="sk-…") as client:
            agent = await client.agents.create_async(name="my-bot")
            await client.agents.deploy_async(agent.id)

    asyncio.run(main())
"""

from __future__ import annotations

from .client import Agents, Client
from .config import Config
from .exceptions import (
    AgntSparkError,
    AuthenticationError,
    DeploymentError,
    NotFoundError,
    RateLimitError,
)
from .models import (
    AccessKey,
    AccessKeyCreated,
    AgentAccess,
    AgentConfig,
    AgentListResponse,
    AgentLog,
    AgentResponse,
    AgentRuntime,
    AgentStatus,
    DeployConfig,
    EnvVar,
    InvokeResponse,
    LogListResponse,
    Metrics,
    ResourceLimits,
    ScaleDirection,
    ScaleRequest,
    ScaleResponse,
)
from .streaming import EventType, SSEStream, StreamEvent

__version__ = "1.3.0"

__all__ = [
    "__version__",
    # Client
    "Client",
    "Agents",
    # Config
    "Config",
    # Models
    "AgentConfig",
    "AgentResponse",
    "AgentRuntime",
    "AgentStatus",
    "ScaleDirection",
    "ResourceLimits",
    "EnvVar",
    "DeployConfig",
    "AccessKey",
    "AccessKeyCreated",
    "AgentAccess",
    "InvokeResponse",
    "Metrics",
    "AgentLog",
    "ScaleRequest",
    "ScaleResponse",
    "AgentListResponse",
    "LogListResponse",
    # Streaming
    "EventType",
    "SSEStream",
    "StreamEvent",
    # Exceptions
    "AgntSparkError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "DeploymentError",
]
