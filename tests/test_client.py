"""
Unit tests for the AgntSpark SDK client.

These tests use ``respx`` to mock httpx HTTP responses, so no network
access is required.

Run::

    pytest tests/test_client.py -v
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from agntspark import (
    AgentStatus,
    AuthenticationError,
    Client,
    DeploymentError,
    NotFoundError,
    RateLimitError,
)
from agntspark.client import _RateLimiter, _should_retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "https://agntapi.agntspark.com/v1"

AGENT_FIXTURE: dict[str, Any] = {
    "id": "agt_abc123",
    "name": "test-agent",
    "runtime": "python3.12",
    "framework": "langchain",
    "model": "gpt-4o",
    "status": "running",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "url": "https://agent-abc123.agntspark.app",
    "replicas": 2,
    "version": 3,
    "tags": ["prod"],
    "metadata": {"team": "ops"},
}


def _make_client(**kwargs: Any) -> Client:
    return Client(api_key="sk-test-key", base_url=BASE_URL, **kwargs)


# ---------------------------------------------------------------------------
# Client initialisation
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_requires_api_key(self) -> None:
        with pytest.raises(AuthenticationError):
            Client(api_key=None)

    def test_with_config(self) -> None:
        from agntspark.config import Config

        cfg = Config(api_key="sk-test", base_url=BASE_URL)
        client = Client(config=cfg)
        assert client._config.api_key == "sk-test"

    def test_headers(self) -> None:
        client = _make_client()
        h = client._headers
        assert h["Authorization"] == "Bearer sk-test-key"
        assert "agntspark-sdk-python" in h["User-Agent"]

    def test_context_manager_sync(self) -> None:
        with _make_client() as client:
            assert client._config.api_key is not None

    @pytest.mark.asyncio
    async def test_context_manager_async(self) -> None:
        async with _make_client() as client:
            assert client._config.api_key is not None


# ---------------------------------------------------------------------------
# agents.create
# ---------------------------------------------------------------------------


class TestCreate:
    @respx.mock
    def test_create_success(self) -> None:
        respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(201, json=AGENT_FIXTURE))
        with _make_client() as client:
            agent = client.agents.create(
                name="test-agent",
                framework="langchain",
                model="gpt-4o",
            )
        assert agent.id == "agt_abc123"
        assert agent.status == AgentStatus.RUNNING

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_async(self) -> None:
        respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(201, json=AGENT_FIXTURE))
        async with _make_client() as client:
            agent = await client.agents.create_async(name="test-agent")
        assert agent.id == "agt_abc123"


# ---------------------------------------------------------------------------
# agents.deploy
# ---------------------------------------------------------------------------


class TestDeploy:
    @respx.mock
    def test_deploy_success(self) -> None:
        fixture = {**AGENT_FIXTURE, "status": "building"}
        respx.post(f"{BASE_URL}/agents/agt_abc123/deploy").mock(
            return_value=httpx.Response(200, json=fixture)
        )
        with _make_client() as client:
            agent = client.agents.deploy("agt_abc123")
        assert agent.status == AgentStatus.BUILDING

    @respx.mock
    def test_deploy_failure(self) -> None:
        respx.post(f"{BASE_URL}/agents/agt_abc123/deploy").mock(
            return_value=httpx.Response(502, json={"error": "Image pull failed"})
        )
        with _make_client() as client:
            with pytest.raises(DeploymentError) as exc_info:
                client.agents.deploy("agt_abc123")
        assert "Image pull failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# agents.list
# ---------------------------------------------------------------------------


class TestList:
    @respx.mock
    def test_list_success(self) -> None:
        respx.get(f"{BASE_URL}/agents").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agents": [AGENT_FIXTURE],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                    "has_next": False,
                },
            )
        )
        with _make_client() as client:
            result = client.agents.list()
        assert len(result.agents) == 1
        assert result.total == 1
        assert result.has_next is False

    @respx.mock
    def test_list_with_filters(self) -> None:
        route = respx.get(f"{BASE_URL}/agents").mock(
            return_value=httpx.Response(
                200,
                json={"agents": [], "total": 0, "has_next": False},
            )
        )
        with _make_client() as client:
            client.agents.list(status="running", tag="prod", page=2, page_size=10)
        request = route.calls[0].request
        assert "status=running" in str(request.url)
        assert "tag=prod" in str(request.url)
        assert "page=2" in str(request.url)


# ---------------------------------------------------------------------------
# agents.get
# ---------------------------------------------------------------------------


class TestGet:
    @respx.mock
    def test_get_success(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_abc123").mock(
            return_value=httpx.Response(200, json=AGENT_FIXTURE)
        )
        with _make_client() as client:
            agent = client.agents.get("agt_abc123")
        assert agent.name == "test-agent"

    @respx.mock
    def test_get_not_found(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_missing").mock(
            return_value=httpx.Response(404, json={"error": "Agent not found"})
        )
        with _make_client() as client:
            with pytest.raises(NotFoundError):
                client.agents.get("agt_missing")


# ---------------------------------------------------------------------------
# agents.delete
# ---------------------------------------------------------------------------


class TestDelete:
    @respx.mock
    def test_delete_success(self) -> None:
        respx.delete(f"{BASE_URL}/agents/agt_abc123").mock(return_value=httpx.Response(204))
        with _make_client() as client:
            client.agents.delete("agt_abc123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_async(self) -> None:
        respx.delete(f"{BASE_URL}/agents/agt_abc123").mock(return_value=httpx.Response(204))
        async with _make_client() as client:
            await client.agents.delete_async("agt_abc123")


# ---------------------------------------------------------------------------
# agents.logs
# ---------------------------------------------------------------------------


class TestLogs:
    @respx.mock
    def test_logs_success(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_abc123/logs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "logs": [
                        {
                            "agent_id": "agt_abc123",
                            "replica_id": "rpt_001",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "level": "INFO",
                            "message": "Agent started",
                        }
                    ],
                    "total": 1,
                    "has_next": False,
                },
            )
        )
        with _make_client() as client:
            logs = client.agents.logs("agt_abc123")
        assert len(logs) == 1
        assert logs[0].message == "Agent started"


# ---------------------------------------------------------------------------
# agents.metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    @respx.mock
    def test_metrics_success(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_abc123/metrics").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": "agt_abc123",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "cpu_percent": 42.5,
                    "memory_mb": 2048,
                    "memory_percent": 65.0,
                    "request_count": 500,
                    "request_rate": 8.3,
                    "p50_latency_ms": 100.0,
                    "p95_latency_ms": 200.0,
                    "p99_latency_ms": 400.0,
                    "replicas": 2,
                },
            )
        )
        with _make_client() as client:
            m = client.agents.metrics("agt_abc123", window="1h")
        assert m.cpu_percent == 42.5
        assert m.request_count == 500


# ---------------------------------------------------------------------------
# agents.scale
# ---------------------------------------------------------------------------


class TestScale:
    @respx.mock
    def test_scale_up(self) -> None:
        respx.post(f"{BASE_URL}/agents/agt_abc123/scale").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": "agt_abc123",
                    "previous_replicas": 2,
                    "current_replicas": 5,
                    "direction": "up",
                    "status": "scaling",
                },
            )
        )
        with _make_client() as client:
            resp = client.agents.scale("agt_abc123", "up", count=3)
        assert resp.current_replicas == 5
        assert resp.previous_replicas == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @respx.mock
    def test_auth_error(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_x").mock(
            return_value=httpx.Response(401, json={"error": "Invalid API key"})
        )
        with _make_client() as client:
            with pytest.raises(AuthenticationError) as exc:
                client.agents.get("agt_x")
        assert "Invalid API key" in str(exc.value)

    @respx.mock
    def test_rate_limit_error(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_x").mock(
            return_value=httpx.Response(
                429,
                json={"error": "Rate limit exceeded"},
                headers={"Retry-After": "30"},
            )
        )
        with _make_client(max_retries=0) as client:
            with pytest.raises(RateLimitError) as exc:
                client.agents.get("agt_x")
        assert exc.value.retry_after == 30.0

    @respx.mock
    def test_server_error_retry_then_success(self) -> None:
        route = respx.get(f"{BASE_URL}/agents/agt_x").mock(
            side_effect=[
                httpx.Response(503, json={"error": "Service unavailable"}),
                httpx.Response(503, json={"error": "Service unavailable"}),
                httpx.Response(200, json=AGENT_FIXTURE),
            ]
        )
        with _make_client(max_retries=3, retry_backoff=0.01) as client:
            agent = client.agents.get("agt_x")
        assert route.call_count == 3
        assert agent.id == "agt_abc123"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


class TestShouldRetry:
    def test_retryable_codes(self) -> None:
        for code in (408, 429, 500, 502, 503, 504):
            assert _should_retry(code) is True

    def test_non_retryable_codes(self) -> None:
        for code in (200, 201, 400, 401, 403, 404, 422):
            assert _should_retry(code) is False


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self) -> None:
        rl = _RateLimiter(60)
        await rl.acquire()
        assert rl._tokens < 60.0

    @pytest.mark.asyncio
    async def test_capacity(self) -> None:
        rl = _RateLimiter(10)
        assert rl._capacity == 10
