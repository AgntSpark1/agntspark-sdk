<p align="center">
  <img src="https://raw.githubusercontent.com/AgntSpark1/agntspark-sdk/main/docs/assets/agntspark-logo.png" alt="AgntSpark" width="200" onerror="this.style.display='none'"/>
</p>

# AgntSpark Python SDK

[![CI](https://github.com/AgntSpark1/agntspark-sdk/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/AgntSpark1/agntspark-sdk/actions)
[![PyPI](https://img.shields.io/pypi/v/agntspark)](https://pypi.org/project/agntspark/)
[![Python](https://img.shields.io/pypi/pyversions/agntspark)](https://pypi.org/project/agntspark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Documentation](https://img.shields.io/badge/docs-agntspark.com-blue)](https://docs.agntspark.com/sdk/python)
[![Code Style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/charliermarsh/ruff/main/assets/badge/v1.json)](https://github.com/astral-sh/ruff)

> The official Python SDK for the [AgntSpark](https://agntspark.com) AI Agent hosting platform. Deploy, monitor, and scale LLM-powered agents with a fully typed, async-first API.

## Overview

AgntSpark is a managed platform for hosting AI agents — built on top of Kubernetes and optimized for LLM workloads. This SDK provides a thin, Pythonic wrapper around the AgntSpark REST API with:

- **Full type safety** — every request and response is validated with Pydantic v2 models
- **Sync + Async** — every method has a synchronous and an `async` variant
- **Auto-retry** — transient failures (5xx, 429, connection errors) are retried with exponential backoff
- **Rate limiting** — built-in token-bucket limiter prevents accidental API throttling
- **SSE streaming** — real-time log and metric streams via Server-Sent Events
- **CLI tool** — deploy, list, and tail logs directly from the terminal
- **Zero boilerplate** — config is auto-loaded from env vars or `~/.agntspark/config.yaml`

## Installation

```bash
pip install agntspark
```

For development with test and linting tools:

```bash
pip install agntspark[dev]
```

## Quick Start

### Synchronous

```python
from agntspark import Client, DeployConfig, ResourceLimits

with Client(api_key="sk-...") as client:
    # Create and deploy an agent
    agent = client.agents.create(
        name="support-bot",
        framework="langchain",
        model="gpt-4o",
        system_prompt="You are a helpful customer support assistant.",
        deploy=DeployConfig(
            replicas=2,
            resources=ResourceLimits(cpu=2.0, memory_mb=2048),
            health_check_path="/health",
            auto_scale=True,
            min_replicas=2,
            max_replicas=10,
        ),
        tags=["production", "support"],
    )
    print(f"Agent deployed: {agent.id}")
    print(f"URL: {agent.url}")

    # List all agents
    result = client.agents.list(status="running")
    for a in result.agents:
        print(f"  {a.id}: {a.name} ({a.status.value})")

    # Fetch metrics
    metrics = client.agents.metrics(agent.id, window="1h")
    print(f"CPU: {metrics.cpu_percent}%  P95: {metrics.p95_latency_ms}ms")
```

### Asynchronous

```python
import asyncio
from agntspark import Client

async def main():
    async with Client(api_key="sk-...") as client:
        agent = await client.agents.create_async(name="my-bot", model="gpt-4o")
        await client.agents.deploy_async(agent.id)

        # Stream logs in real time
        async for event in client.agents.stream_logs(agent.id):
            if event.type == "log":
                print(f"[{event.data['level']}] {event.data['message']}")

asyncio.run(main())
```

### CLI

```bash
# Configure your API key
agntspark init

# Deploy from a YAML config file
agntspark deploy ./agent-config.yaml --wait

# List running agents
agntspark list --status running

# Tail live logs
agntspark logs agt_abc123 --follow

# View metrics
agntspark metrics agt_abc123 --window 1h
```

## Configuration

The SDK resolves configuration in the following order (later sources override earlier ones):

1. **Defaults** — built into the SDK
2. **Config file** — `~/.agntspark/config.yaml` (or path in `AGNTSPARK_CONFIG_FILE`)
3. **Environment variables** — prefixed with `AGNTSPARK_`
4. **Constructor kwargs** — passed directly to `Client(api_key=..., base_url=...)`

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGNTSPARK_API_KEY` | — | Your API key (required) |
| `AGNTSPARK_BASE_URL` | `https://agntapi.agntspark.com/v1` | API base URL |
| `AGNTSPARK_TIMEOUT` | `30` | Request timeout in seconds |
| `AGNTSPARK_MAX_RETRIES` | `3` | Max retry attempts for transient errors |
| `AGNTSPARK_RETRY_BACKOFF` | `0.5` | Base backoff multiplier (seconds) |
| `AGNTSPARK_RATE_LIMIT_RPM` | `600` | Max requests per minute |
| `AGNTSPARK_PROJECT` | — | Default project ID |

### Config File

```yaml
# ~/.agntspark/config.yaml
api_key: sk-your-api-key-here
base_url: https://agntapi.agntspark.com/v1
timeout: 30
max_retries: 3
retry_backoff: 0.5
rate_limit_rpm: 600
default_project: prj_abc123
```

## API Reference

### `Client`

The main entry point. Can be used as a context manager (sync or async).

```python
client = Client(api_key="sk-...", base_url="https://agntapi.agntspark.com/v1")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str \| None` | from env/config | AgntSpark API key |
| `config` | `Config \| None` | `None` | Pre-built Config object |
| `base_url` | `str` | `https://agntapi.agntspark.com/v1` | API base URL |
| `timeout` | `float` | `30.0` | HTTP timeout (seconds) |
| `max_retries` | `int` | `3` | Max retries for transient errors |
| `retry_backoff` | `float` | `0.5` | Exponential backoff base |
| `rate_limit_rpm` | `int` | `600` | Requests per minute |

---

### `client.agents.create()`

Create a new agent on the platform.

```python
agent = client.agents.create(
    name="my-bot",
    runtime="python3.12",      # or AgentRuntime.PYTHON_3_12
    framework="langchain",     # langchain | autogen | crewai | custom
    model="gpt-4o",
    system_prompt="You are a helpful assistant.",
    deploy=DeployConfig(...),  # optional — deploys immediately if provided
    tags=["prod", "v1"],
    metadata={"team": "ops"},
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str` | ✅ | Agent name (1–128 chars) |
| `runtime` | `AgentRuntime` | — | Execution runtime |
| `framework` | `str` | — | Agent framework (`"custom"` by default) |
| `model` | `str` | — | LLM model identifier (`"gpt-4o"` by default) |
| `system_prompt` | `str \| None` | — | System prompt (max 32k chars) |
| `deploy` | `DeployConfig \| None` | — | Deploy immediately after creation |
| `tags` | `list[str]` | — | Tags for grouping/filtering |
| `metadata` | `dict[str, str]` | — | Free-form metadata |

**Returns:** `AgentResponse`

**Async:** `await client.agents.create_async(...)`

---

### `client.agents.deploy()`

Deploy or redeploy an agent.

```python
agent = client.agents.deploy("agt_abc123", config=DeployConfig(replicas=3, ...))
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | `str` | ✅ | Agent ID (`agt_...`) |
| `config` | `DeployConfig \| None` | — | Deployment config (uses stored config if omitted) |

**Returns:** `AgentResponse`

**Async:** `await client.agents.deploy_async(...)`

---

### `client.agents.list()`

List agents with optional filtering and pagination.

```python
result = client.agents.list(status="running", tag="prod", page=1, page_size=20)
for agent in result.agents:
    print(agent.id, agent.name)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | `str \| None` | — | Filter by status |
| `tag` | `str \| None` | — | Filter by tag |
| `page` | `int` | `1` | Page number (1-based) |
| `page_size` | `int` | `20` | Results per page (1–100) |

**Returns:** `AgentListResponse` (contains `.agents`, `.total`, `.has_next`)

**Async:** `await client.agents.list_async(...)`

---

### `client.agents.get()`

Retrieve a single agent by ID.

```python
agent = client.agents.get("agt_abc123")
print(agent.status.value)  # "running"
```

**Returns:** `AgentResponse`

**Async:** `await client.agents.get_async(...)`

---

### `client.agents.delete()`

Permanently delete an agent and all its resources.

```python
client.agents.delete("agt_abc123")
```

**Returns:** `None`

**Async:** `await client.agents.delete_async(...)`

---

### `client.agents.logs()`

Fetch historical agent logs (paginated). For real-time streaming, use `stream_logs()`.

```python
logs = client.agents.logs("agt_abc123", level="ERROR", limit=50)
for log in logs:
    print(f"[{log.timestamp}] [{log.level}] {log.message}")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str` | — | Agent ID |
| `level` | `str \| None` | — | Filter by level (DEBUG, INFO, WARN, ERROR) |
| `replica_id` | `str \| None` | — | Filter by replica |
| `cursor` | `str \| None` | — | Pagination cursor |
| `limit` | `int` | `100` | Max results |

**Returns:** `list[AgentLog]`

**Async:** `await client.agents.logs_async(...)`

---

### `client.agents.metrics()`

Retrieve aggregated runtime metrics for an agent.

```python
m = client.agents.metrics("agt_abc123", window="1h")
print(f"CPU: {m.cpu_percent}%  Memory: {m.memory_mb}MB")
print(f"P95: {m.p95_latency_ms}ms  Errors: {m.error_count}")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str` | — | Agent ID |
| `window` | `str` | `"1h"` | Time window (`5m`, `1h`, `24h`, `7d`) |

**Returns:** `Metrics`

**Async:** `await client.agents.metrics_async(...)`

---

### `client.agents.scale()`

Manually scale an agent up or down.

```python
resp = client.agents.scale("agt_abc123", direction="up", count=3)
print(f"Replicas: {resp.previous_replicas} → {resp.current_replicas}")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str` | — | Agent ID |
| `direction` | `str` | — | `"up"` or `"down"` |
| `count` | `int` | `1` | Replicas to add/remove (1–50) |
| `reason` | `str \| None` | — | Audit reason |

**Returns:** `ScaleResponse`

**Async:** `await client.agents.scale_async(...)`

---

### `client.agents.stream_logs()`

Stream agent logs in real time via SSE. **Async only.**

```python
async for event in client.agents.stream_logs("agt_abc123", level="INFO"):
    if event.type == EventType.LOG:
        print(event.data["message"])
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str` | — | Agent ID |
| `level` | `str \| None` | — | Filter by log level |
| `follow` | `bool` | `True` | Keep streaming after initial tail |

**Yields:** `StreamEvent`

---

### `client.agents.stream_metrics()`

Stream agent metrics in real time via SSE. **Async only.**

```python
async for event in client.agents.stream_metrics("agt_abc123", interval=10):
    if event.type == EventType.METRIC:
        print(event.data["cpu_percent"])
```

**Yields:** `StreamEvent`

---

## Models

### `AgentConfig`

Configuration for creating a new agent.

| Field | Type | Default |
|-------|------|---------|
| `name` | `str` | — |
| `runtime` | `AgentRuntime` | `PYTHON_3_12` |
| `framework` | `str` | `"custom"` |
| `model` | `str` | `"gpt-4o"` |
| `system_prompt` | `str \| None` | `None` |
| `deploy` | `DeployConfig \| None` | `None` |
| `tags` | `list[str]` | `[]` |
| `metadata` | `dict[str, str]` | `{}` |

### `DeployConfig`

| Field | Type | Default |
|-------|------|---------|
| `replicas` | `int` | `1` |
| `resources` | `ResourceLimits` | defaults |
| `env` | `list[EnvVar]` | `[]` |
| `image` | `str \| None` | `None` |
| `build_path` | `str \| None` | `None` |
| `command` | `str \| None` | `None` |
| `args` | `list[str]` | `[]` |
| `health_check_path` | `str \| None` | `None` |
| `auto_scale` | `bool` | `False` |
| `min_replicas` | `int` | `1` |
| `max_replicas` | `int` | `10` |
| `port` | `int` | `8080` |

### `ResourceLimits`

| Field | Type | Default | Range |
|-------|------|---------|-------|
| `cpu` | `float` | `1.0` | 0.1–64 |
| `memory_mb` | `int` | `512` | 128–65536 |
| `gpu` | `int` | `0` | 0–8 |
| `gpu_type` | `str \| None` | `None` | required if `gpu > 0` |
| `disk_gb` | `int` | `10` | 1–1000 |
| `ephemeral_storage_gb` | `int` | `5` | 1–500 |

### `Metrics`

| Field | Type | Description |
|-------|------|-------------|
| `cpu_percent` | `float` | CPU utilisation (%) |
| `memory_mb` | `int` | Memory usage (MB) |
| `memory_percent` | `float` | Memory utilisation (%) |
| `gpu_percent` | `float` | GPU utilisation (%) |
| `request_count` | `int` | Total requests in window |
| `request_rate` | `float` | Requests per second |
| `error_count` | `int` | Total errors |
| `error_rate` | `float` | Errors per second |
| `p50_latency_ms` | `float` | Median latency |
| `p95_latency_ms` | `float` | 95th percentile latency |
| `p99_latency_ms` | `float` | 99th percentile latency |
| `replicas` | `int` | Current replica count |

### `AgentStatus`

| Value | Healthy | Description |
|-------|---------|-------------|
| `pending` | — | Created, not yet built |
| `building` | — | Container image building |
| `starting` | ✅ | Container starting up |
| `running` | ✅ | Healthy and serving traffic |
| `scaling` | ✅ | Scaling replicas |
| `stopping` | — | Graceful shutdown in progress |
| `stopped` | — | Manually stopped |
| `failed` | — | Deployment failed |
| `crashed` | — | Runtime crash |

## Exceptions

All exceptions inherit from `AgntSparkError`:

```
AgntSparkError
├── AuthenticationError     # 401 / 403
├── NotFoundError           # 404
├── RateLimitError          # 429 (has .retry_after)
└── DeploymentError         # 5xx
```

```python
from agntspark import AgntSparkError, RateLimitError

try:
    agent = client.agents.get("agt_x")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after}s")
except AgntSparkError as e:
    print(f"Error [{e.status_code}]: {e.message}")
```

## SDK vs Raw API Calls

| Feature | Raw `requests`/`httpx` | AgntSpark SDK |
|---------|----------------------|---------------|
| **Type safety** | ❌ Manual JSON parsing | ✅ Pydantic v2 models |
| **Auth headers** | ❌ Manual `Authorization` header | ✅ Automatic |
| **Retry logic** | ❌ Write your own loop | ✅ Built-in exponential backoff |
| **Rate limiting** | ❌ Throttle yourself or get 429s | ✅ Token-bucket limiter |
| **Streaming** | ❌ Parse SSE manually | ✅ Typed `StreamEvent` iterator |
| **Pagination** | ❌ Track cursors yourself | ✅ `has_next` + `next_cursor` |
| **Error mapping** | ❌ Check status codes | ✅ Typed exceptions |
| **Config** | ❌ Hardcode or env vars | ✅ File + env + kwargs merge |
| **CLI** | ❌ Write your own scripts | ✅ `agntspark deploy/list/logs` |
| **Async** | ⚠️ Manual httpx async | ✅ Every method has `_async` variant |
| **Lines of code to deploy** | ~40 | **~5** |

### Raw API equivalent

```python
# Raw httpx — 40+ lines
import httpx

headers = {"Authorization": "Bearer sk-...", "Content-Type": "application/json"}
resp = httpx.post(
    "https://agntapi.agntspark.com/v1/agents",
    headers=headers,
    json={"name": "my-bot", "model": "gpt-4o", ...},
)
if resp.status_code == 429:
    retry_after = int(resp.headers.get("Retry-After", "5"))
    # ... manual retry loop ...
agent = resp.json()  # no validation
```

```python
# AgntSpark SDK — 3 lines
from agntspark import Client
with Client() as client:
    agent = client.agents.create(name="my-bot", model="gpt-4o")
```

## Examples

The [`examples/`](./examples) directory contains runnable scripts:

| File | Description |
|------|-------------|
| [`deploy_agent.py`](./examples/deploy_agent.py) | Create and deploy an agent with resource limits |
| [`monitor_agent.py`](./examples/monitor_agent.py) | Poll metrics and logs on a loop |
| [`stream_logs.py`](./examples/stream_logs.py) | Real-time SSE log streaming |
| [`multi_deploy.py`](./examples/multi_deploy.py) | Deploy multiple agents in parallel with asyncio |

## Development

```bash
# Clone
git clone https://github.com/AgntSpark1/agntspark-sdk.git
cd agntspark-sdk

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check agntspark/ tests/

# Type check
mypy agntspark/
```

## Changelog

### v1.2.0 (2026-01-15)
- Added SSE streaming for metrics (`stream_metrics`)
- Added rate limiting with configurable RPM
- Improved retry backoff with jitter
- Added `ResourceLimits.ephemeral_storage_gb` field

### v1.1.0 (2025-12-01)
- Added async variants for all methods
- Added `stream_logs` SSE endpoint
- Added CLI tool (`agntspark` command)
- Pydantic v2 migration

### v1.0.0 (2025-10-15)
- Initial release
- Sync client with create, deploy, list, get, delete, logs, metrics, scale

## License

[MIT](./LICENSE) — Copyright © 2026 AgntSpark LLC
