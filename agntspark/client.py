"""
AgntSpark API client — the core of the SDK.

The :class:`Client` class is the primary entry point.  It provides both
synchronous and asynchronous interfaces, automatic retry with exponential
backoff, token-bucket rate limiting, and SSE streaming support.

Quick start::

    from agntspark import Client

    with Client(api_key="sk-…") as client:
        agent = client.agents.create(name="my-bot", model="gpt-4o")
        client.agents.deploy(agent.id)
        for log in client.agents.logs(agent.id):
            print(log.message)

Async equivalent::

    async with Client(api_key="sk-…") as client:
        agent = await client.agents.create_async(name="my-bot")
        await client.agents.deploy_async(agent.id)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, Iterator, List, Optional, AsyncIterator

import httpx

from .config import Config
from .exceptions import (
    AgntSparkError,
    AuthenticationError,
    DeploymentError,
    NotFoundError,
    RateLimitError,
)
from .models import (
    AgentConfig,
    AgentListResponse,
    AgentLog,
    AgentResponse,
    AgentRuntime,
    DeployConfig,
    LogListResponse,
    Metrics,
    ScaleDirection,
    ScaleRequest,
    ScaleResponse,
)
from .streaming import SSEStream, StreamEvent

logger = logging.getLogger("agntspark")


class _RateLimiter:
    """
    Simple token-bucket rate limiter.

    Tokens are replenished at a rate of ``rate_limit_rpm / 60`` per second.
    When the bucket is empty, callers block until a token becomes available.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self._capacity = max(requests_per_minute, 1)
        self._tokens: float = float(self._capacity)
        self._refill_rate = self._capacity / 60.0  # tokens per second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        async with self._lock:
            await self._refill()
            while self._tokens < 1.0:
                deficit = 1.0 - self._tokens
                wait = deficit / self._refill_rate
                await asyncio.sleep(wait)
                await self._refill()
            self._tokens -= 1.0


def _should_retry(status_code: int) -> bool:
    """Return ``True`` when a transient HTTP status warrants a retry."""
    return status_code in (408, 429, 500, 502, 503, 504)


class Client:
    """
    Synchronous and asynchronous AgntSpark API client.

    The client is usable as a context manager in both sync and async
    contexts::

        # synchronous
        with Client(api_key="sk-…") as client:
            ...

        # asynchronous
        async with Client(api_key="sk-…") as client:
            ...

    Parameters
    ----------
    api_key:
        AgntSpark API key.  If omitted, falls back to ``AGNTSPARK_API_KEY``
        environment variable or the config file at
        ``~/.agntspark/config.yaml``.
    config:
        Pre-built :class:`~agntspark.config.Config`.  When provided,
        keyword arguments for ``base_url``, ``timeout``, etc. are ignored.
    **kwargs:
        Override individual config fields (``base_url``, ``timeout``,
        ``max_retries``, ``retry_backoff``, ``rate_limit_rpm``).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        config: Optional[Config] = None,
        **kwargs: Any,
    ) -> None:
        if config is not None:
            self._config = config
        else:
            self._config = Config.load(api_key=api_key, **kwargs)

        if not self._config.api_key:
            raise AuthenticationError(
                "No API key provided. Set AGNTSPARK_API_KEY env var, "
                "configure ~/.agntspark/config.yaml, or pass api_key=… to Client()."
            )

        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None
        self._rate_limiter = _RateLimiter(self._config.rate_limit_rpm)
        self.agents = Agents(self)

    # ------------------------------------------------------------------
    # Context-manager plumbing
    # ------------------------------------------------------------------

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    def close(self) -> None:
        """Close the synchronous HTTP client."""
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    async def aclose(self) -> None:
        """Close the asynchronous HTTP client."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "agntspark-sdk-python/1.2.0",
        }
        if self._config.default_project:
            h["X-AgntSpark-Project"] = self._config.default_project
        h.update(self._config.extra_headers)
        return h

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                base_url=self._config.base_url,
                timeout=self._config.timeout,
                headers=self._headers,
            )
        return self._sync_client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout,
                headers=self._headers,
            )
        return self._async_client

    def _build_url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _handle_error(self, response: httpx.Response) -> None:
        """Raise the appropriate SDK exception for an error response."""
        status = response.status_code
        try:
            body = response.json()
            message = body.get("error", body.get("message", response.text))
        except Exception:
            body = response.text
            message = response.text or f"HTTP {status}"

        if status in (401, 403):
            raise AuthenticationError(message, status_code=status, response_body=body)
        elif status == 404:
            raise NotFoundError("Agent or resource", status_code=status, response_body=body)
        elif status == 429:
            retry_after = response.headers.get("Retry-After")
            retry_secs = float(retry_after) if retry_after else None
            raise RateLimitError(
                message, retry_after=retry_secs, status_code=status, response_body=body
            )
        elif 400 <= status < 500:
            raise AgntSparkError(message, status_code=status, response_body=body)
        elif status >= 500:
            raise DeploymentError(message, status_code=status, response_body=body)

    # ------------------------------------------------------------------
    # Synchronous request with retry
    # ------------------------------------------------------------------

    def _request_sync(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        client = self._get_sync_client()
        url = path if path.startswith("http") else path

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._config.max_retries + 2):
            try:
                response = client.request(method, url, json=json_body, params=params)
                if response.status_code == 429 and _should_retry(response.status_code):
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else self._config.retry_backoff * (
                        2 ** (attempt - 1)
                    )
                    logger.warning("Rate limited, retrying in %.1fs (attempt %d)", delay, attempt)
                    time.sleep(delay)
                    continue
                if _should_retry(response.status_code) and attempt <= self._config.max_retries:
                    delay = self._config.retry_backoff * (2 ** (attempt - 1))
                    delay += random.uniform(0, 0.1)  # jitter
                    logger.warning(
                        "Retrying %s %s after %d (attempt %d/%d, delay %.1fs)",
                        method, path, response.status_code, attempt, self._config.max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                if response.status_code >= 400:
                    self._handle_error(response)
                # 204 No Content or empty body → return empty dict
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                if attempt > self._config.max_retries:
                    raise AgntSparkError(f"Request failed after {attempt} retries: {exc}") from exc
                delay = self._config.retry_backoff * (2 ** (attempt - 1))
                logger.warning("Connection error, retrying in %.1fs: %s", delay, exc)
                time.sleep(delay)

        raise AgntSparkError(f"Request failed: {last_exc}")
    # ------------------------------------------------------------------
    # Asynchronous request with retry + rate limiting
    # ------------------------------------------------------------------

    async def _request_async(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self._rate_limiter.acquire()
        client = self._get_async_client()
        url = path if path.startswith("http") else path

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._config.max_retries + 2):
            try:
                response = await client.request(method, url, json=json_body, params=params)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else self._config.retry_backoff * (
                        2 ** (attempt - 1)
                    )
                    logger.warning("Rate limited, retrying in %.1fs (attempt %d)", delay, attempt)
                    await asyncio.sleep(delay)
                    continue
                if _should_retry(response.status_code) and attempt <= self._config.max_retries:
                    delay = self._config.retry_backoff * (2 ** (attempt - 1))
                    delay += random.uniform(0, 0.1)
                    logger.warning(
                        "Retrying %s %s after %d (attempt %d/%d, delay %.1fs)",
                        method, path, response.status_code, attempt, self._config.max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if response.status_code >= 400:
                    self._handle_error(response)
                # 204 No Content or empty body → return empty dict
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                if attempt > self._config.max_retries:
                    raise AgntSparkError(f"Request failed after {attempt} retries: {exc}") from exc
                delay = self._config.retry_backoff * (2 ** (attempt - 1))
                logger.warning("Connection error, retrying in %.1fs: %s", delay, exc)
                await asyncio.sleep(delay)

        raise AgntSparkError(f"Request failed: {last_exc}")


class Agents:
    """
    Agent management namespace.

    Accessed via :attr:`Client.agents`.  Every method has a synchronous
    variant (the default) and an ``_async`` variant for use within
    ``async with`` blocks::

        # sync
        agent = client.agents.create(...)

        # async
        agent = await client.agents.create_async(...)
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    @property
    def _base(self) -> str:
        return self._client._config.base_url.rstrip("/")

    # ==================================================================
    # CREATE
    # ==================================================================

    def create(
        self,
        name: str,
        *,
        runtime: Optional[AgentRuntime] = None,
        framework: str = "custom",
        model: str = "gpt-4o",
        system_prompt: Optional[str] = None,
        deploy: Optional[DeployConfig] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> AgentResponse:
        """
        Create a new agent on the AgntSpark platform.

        Parameters
        ----------
        name:
            Human-readable agent name (1–128 chars).
        runtime:
            Execution runtime (``python3.12``, ``node22``, …).
        framework:
            Agent framework identifier.
        model:
            LLM model identifier.
        system_prompt:
            System prompt text injected into every conversation.
        deploy:
            If provided, the agent is deployed immediately after creation.
        tags:
            List of tag strings for grouping.
        metadata:
            Free-form key/value metadata.

        Returns
        -------
        AgentResponse
            The created agent.
        """
        config = AgentConfig(
            name=name,
            runtime=runtime,
            framework=framework,
            model=model,
            system_prompt=system_prompt,
            deploy=deploy,
            tags=tags or [],
            metadata=metadata or {},
        )
        data = self._client._request_sync("POST", f"{self._base}/agents", json_body=config.model_dump(exclude_none=True))
        return AgentResponse(**data)

    async def create_async(
        self,
        name: str,
        *,
        runtime: Optional[AgentRuntime] = None,
        framework: str = "custom",
        model: str = "gpt-4o",
        system_prompt: Optional[str] = None,
        deploy: Optional[DeployConfig] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> AgentResponse:
        config = AgentConfig(
            name=name,
            runtime=runtime,
            framework=framework,
            model=model,
            system_prompt=system_prompt,
            deploy=deploy,
            tags=tags or [],
            metadata=metadata or {},
        )
        data = await self._client._request_async(
            "POST", f"{self._base}/agents", json_body=config.model_dump(exclude_none=True)
        )
        return AgentResponse(**data)

    # ==================================================================
    # DEPLOY
    # ==================================================================

    def deploy(
        self,
        agent_id: str,
        config: Optional[DeployConfig] = None,
    ) -> AgentResponse:
        """
        Deploy (or redeploy) an agent.

        Parameters
        ----------
        agent_id:
            The agent ID (``agt_…``).
        config:
            Deployment configuration.  If ``None``, the agent's previously
            stored deploy config is used.

        Returns
        -------
        AgentResponse
            Updated agent record with status ``building`` or ``starting``.
        """
        body = config.model_dump(exclude_none=True) if config else {}
        data = self._client._request_sync("POST", f"{self._base}/agents/{agent_id}/deploy", json_body=body)
        return AgentResponse(**data)

    async def deploy_async(
        self,
        agent_id: str,
        config: Optional[DeployConfig] = None,
    ) -> AgentResponse:
        body = config.model_dump(exclude_none=True) if config else {}
        data = await self._client._request_async(
            "POST", f"{self._base}/agents/{agent_id}/deploy", json_body=body
        )
        return AgentResponse(**data)

    # ==================================================================
    # LIST
    # ==================================================================

    def list(
        self,
        *,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AgentListResponse:
        """
        List agents with optional filtering.

        Parameters
        ----------
        status:
            Filter by agent status (``running``, ``stopped``, …).
        tag:
            Filter by tag.
        page:
            Page number (1-based).
        page_size:
            Results per page (1–100).

        Returns
        -------
        AgentListResponse
        """
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag
        data = self._client._request_sync("GET", f"{self._base}/agents", params=params)
        return AgentListResponse(**data)

    async def list_async(
        self,
        *,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AgentListResponse:
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag
        data = await self._client._request_async("GET", f"{self._base}/agents", params=params)
        return AgentListResponse(**data)

    # ==================================================================
    # GET
    # ==================================================================

    def get(self, agent_id: str) -> AgentResponse:
        """Retrieve a single agent by ID."""
        data = self._client._request_sync("GET", f"{self._base}/agents/{agent_id}")
        return AgentResponse(**data)

    async def get_async(self, agent_id: str) -> AgentResponse:
        data = await self._client._request_async("GET", f"{self._base}/agents/{agent_id}")
        return AgentResponse(**data)

    # ==================================================================
    # DELETE
    # ==================================================================

    def delete(self, agent_id: str) -> None:
        """Permanently delete an agent and all its resources."""
        self._client._request_sync("DELETE", f"{self._base}/agents/{agent_id}")

    async def delete_async(self, agent_id: str) -> None:
        await self._client._request_async("DELETE", f"{self._base}/agents/{agent_id}")

    # ==================================================================
    # LOGS
    # ==================================================================

    def logs(
        self,
        agent_id: str,
        *,
        level: Optional[str] = None,
        replica_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> List[AgentLog]:
        """
        Fetch historical agent logs (paginated).

        For real-time streaming, use :meth:`stream_logs`.
        """
        params: Dict[str, Any] = {"limit": limit}
        if level:
            params["level"] = level
        if replica_id:
            params["replica_id"] = replica_id
        if cursor:
            params["cursor"] = cursor
        data = self._client._request_sync(
            "GET", f"{self._base}/agents/{agent_id}/logs", params=params
        )
        return LogListResponse(**data).logs

    async def logs_async(
        self,
        agent_id: str,
        *,
        level: Optional[str] = None,
        replica_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> List[AgentLog]:
        params: Dict[str, Any] = {"limit": limit}
        if level:
            params["level"] = level
        if replica_id:
            params["replica_id"] = replica_id
        if cursor:
            params["cursor"] = cursor
        data = await self._client._request_async(
            "GET", f"{self._base}/agents/{agent_id}/logs", params=params
        )
        return LogListResponse(**data).logs

    # ==================================================================
    # METRICS
    # ==================================================================

    def metrics(
        self,
        agent_id: str,
        *,
        window: str = "1h",
    ) -> Metrics:
        """
        Retrieve aggregated metrics for an agent.

        Parameters
        ----------
        agent_id:
            Agent ID.
        window:
            Time window (``5m``, ``1h``, ``24h``, ``7d``).
        """
        params = {"window": window}
        data = self._client._request_sync(
            "GET", f"{self._base}/agents/{agent_id}/metrics", params=params
        )
        return Metrics(**data)

    async def metrics_async(
        self,
        agent_id: str,
        *,
        window: str = "1h",
    ) -> Metrics:
        params = {"window": window}
        data = await self._client._request_async(
            "GET", f"{self._base}/agents/{agent_id}/metrics", params=params
        )
        return Metrics(**data)

    # ==================================================================
    # SCALE
    # ==================================================================

    def scale(
        self,
        agent_id: str,
        direction: str,
        count: int = 1,
        reason: Optional[str] = None,
    ) -> ScaleResponse:
        """
        Manually scale an agent up or down.

        Parameters
        ----------
        agent_id:
            Agent ID.
        direction:
            ``"up"`` to add replicas, ``"down"`` to remove.
        count:
            Number of replicas to add/remove (1–50).
        reason:
            Optional reason for audit logging.
        """
        req = ScaleRequest(direction=ScaleDirection(direction), count=count, reason=reason)
        data = self._client._request_sync(
            "POST", f"{self._base}/agents/{agent_id}/scale", json_body=req.model_dump(exclude_none=True)
        )
        return ScaleResponse(**data)

    async def scale_async(
        self,
        agent_id: str,
        direction: str,
        count: int = 1,
        reason: Optional[str] = None,
    ) -> ScaleResponse:
        req = ScaleRequest(direction=ScaleDirection(direction), count=count, reason=reason)
        data = await self._client._request_async(
            "POST", f"{self._base}/agents/{agent_id}/scale", json_body=req.model_dump(exclude_none=True)
        )
        return ScaleResponse(**data)

    # ==================================================================
    # STREAMING (async only — SSE requires async I/O)
    # ==================================================================

    async def stream_logs(
        self,
        agent_id: str,
        *,
        level: Optional[str] = None,
        follow: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """
        Stream agent logs in real time via SSE.

        Yields :class:`~agntspark.streaming.StreamEvent` objects.  The
        iterator blocks until the stream is closed or the client is shut
        down.

        Example::

            async with Client(api_key="sk-…") as client:
                async for event in client.agents.stream_logs("agt_abc123"):
                    if event.type == EventType.LOG:
                        print(event.data["message"])
        """
        params: Dict[str, Any] = {}
        if level:
            params["level"] = level
        if not follow:
            params["follow"] = "false"
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self._base}/agents/{agent_id}/logs/stream"
        if query:
            url = f"{url}?{query}"

        stream = SSEStream(url, headers=self._client._headers)
        try:
            async for event in stream.events():
                yield event
        finally:
            await stream.close()

    async def stream_metrics(
        self,
        agent_id: str,
        *,
        interval: int = 10,
    ) -> AsyncIterator[StreamEvent]:
        """
        Stream agent metrics in real time via SSE.

        Parameters
        ----------
        agent_id:
            Agent ID.
        interval:
            Polling interval in seconds (server-side, minimum 5).
        """
        url = f"{self._base}/agents/{agent_id}/metrics/stream?interval={max(interval, 5)}"
        stream = SSEStream(url, headers=self._client._headers)
        try:
            async for event in stream.events():
                yield event
        finally:
            await stream.close()


__all__ = ["Client", "Agents"]
