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
import builtins
import logging
import random
import time
from collections.abc import AsyncIterator
from typing import Any, cast

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
    AccessKey,
    AccessKeyCreated,
    AgentAccess,
    AgentConfig,
    AgentListResponse,
    AgentLog,
    AgentResponse,
    AgentRuntime,
    DeployConfig,
    InvokeResponse,
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


class _Unset:
    """Marks an argument that wasn't passed, where ``None`` means something."""

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: Any = _Unset()


def _raise_for_agent_response(response: httpx.Response) -> None:
    """Raise the SDK exception for an error from an agent's own URL.

    The platform's edge answers in plain text (401 private agent without a
    valid key, 404 unknown hostname, 429 rate limited, 503 nothing running);
    the agent answers ``{"error": {"code", "message"}}`` (runtime contract v1).
    """
    status = response.status_code
    if status < 400:
        return
    body: Any
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message", response.text) if isinstance(error, dict) else response.text
    except ValueError:
        body = response.text
        message = response.text.strip() or f"HTTP {status}"

    if status in (401, 403):
        raise AuthenticationError(message, status_code=status, response_body=body)
    if status == 404:
        raise NotFoundError("Agent", status_code=status, response_body=body)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        raise RateLimitError(
            message,
            retry_after=float(retry_after) if retry_after else None,
            status_code=status,
            response_body=body,
        )
    if status < 500:
        raise AgntSparkError(message, status_code=status, response_body=body)
    raise DeploymentError(message, status_code=status, response_body=body)


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
        api_key: str | None = None,
        *,
        config: Config | None = None,
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

        self._async_client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None
        self._rate_limiter = _RateLimiter(self._config.rate_limit_rpm)
        self.agents = Agents(self)

    # ------------------------------------------------------------------
    # Context-manager plumbing
    # ------------------------------------------------------------------

    def __enter__(self) -> Client:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> Client:
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
    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "agntspark-sdk-python/1.3.0",
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
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_sync_client()
        url = path if path.startswith("http") else path

        last_exc: Exception | None = None
        for attempt in range(1, self._config.max_retries + 2):
            try:
                response = client.request(method, url, json=json_body, params=params)
                if response.status_code == 429:
                    if attempt <= self._config.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = (
                            float(retry_after)
                            if retry_after
                            else self._config.retry_backoff * (2 ** (attempt - 1))
                        )
                        logger.warning(
                            "Rate limited, retrying in %.1fs (attempt %d)", delay, attempt
                        )
                        time.sleep(delay)
                        continue
                    # Retries exhausted (or max_retries=0) — raise RateLimitError
                    # via the normal error path rather than falling through to
                    # the generic "Request failed" at the end of this loop.
                    self._handle_error(response)
                if _should_retry(response.status_code) and attempt <= self._config.max_retries:
                    delay = self._config.retry_backoff * (2 ** (attempt - 1))
                    delay += random.uniform(0, 0.1)  # jitter
                    logger.warning(
                        "Retrying %s %s after %d (attempt %d/%d, delay %.1fs)",
                        method,
                        path,
                        response.status_code,
                        attempt,
                        self._config.max_retries,
                        delay,
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
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._rate_limiter.acquire()
        client = self._get_async_client()
        url = path if path.startswith("http") else path

        last_exc: Exception | None = None
        for attempt in range(1, self._config.max_retries + 2):
            try:
                response = await client.request(method, url, json=json_body, params=params)
                if response.status_code == 429:
                    if attempt <= self._config.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = (
                            float(retry_after)
                            if retry_after
                            else self._config.retry_backoff * (2 ** (attempt - 1))
                        )
                        logger.warning(
                            "Rate limited, retrying in %.1fs (attempt %d)", delay, attempt
                        )
                        await asyncio.sleep(delay)
                        continue
                    self._handle_error(response)
                if _should_retry(response.status_code) and attempt <= self._config.max_retries:
                    delay = self._config.retry_backoff * (2 ** (attempt - 1))
                    delay += random.uniform(0, 0.1)
                    logger.warning(
                        "Retrying %s %s after %d (attempt %d/%d, delay %.1fs)",
                        method,
                        path,
                        response.status_code,
                        attempt,
                        self._config.max_retries,
                        delay,
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
        runtime: AgentRuntime | None = None,
        framework: str = "custom",
        model: str = "gpt-4o",
        system_prompt: str | None = None,
        deploy: DeployConfig | None = None,
        tags: builtins.list[str] | None = None,
        metadata: dict[str, str] | None = None,
        api_key: str | None = None,
        access: AgentAccess | str | None = None,
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
        api_key:
            Your API key for the model's provider; stored encrypted and given
            only to this agent.
        access:
            ``"private"`` (the platform default when omitted) or ``"public"``.

        Returns
        -------
        AgentResponse
            The created agent. For a private agent, ``access_key`` holds its
            first access key — the only time it's returned.
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
            api_key=api_key,
            access=AgentAccess(access) if access is not None else None,
        )
        data = self._client._request_sync(
            "POST", f"{self._base}/agents", json_body=config.model_dump(exclude_none=True)
        )
        return AgentResponse(**data)

    async def create_async(
        self,
        name: str,
        *,
        runtime: AgentRuntime | None = None,
        framework: str = "custom",
        model: str = "gpt-4o",
        system_prompt: str | None = None,
        deploy: DeployConfig | None = None,
        tags: builtins.list[str] | None = None,
        metadata: dict[str, str] | None = None,
        api_key: str | None = None,
        access: AgentAccess | str | None = None,
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
            api_key=api_key,
            access=AgentAccess(access) if access is not None else None,
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
        config: DeployConfig | None = None,
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
        data = self._client._request_sync(
            "POST", f"{self._base}/agents/{agent_id}/deploy", json_body=body
        )
        return AgentResponse(**data)

    async def deploy_async(
        self,
        agent_id: str,
        config: DeployConfig | None = None,
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
        status: str | None = None,
        tag: str | None = None,
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
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag
        data = self._client._request_sync("GET", f"{self._base}/agents", params=params)
        return AgentListResponse(**data)

    async def list_async(
        self,
        *,
        status: str | None = None,
        tag: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AgentListResponse:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
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
        level: str | None = None,
        replica_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> builtins.list[AgentLog]:
        """
        Fetch historical agent logs (paginated).

        For real-time streaming, use :meth:`stream_logs`.
        """
        params: dict[str, Any] = {"limit": limit}
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
        level: str | None = None,
        replica_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> builtins.list[AgentLog]:
        params: dict[str, Any] = {"limit": limit}
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
        reason: str | None = None,
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
            "POST",
            f"{self._base}/agents/{agent_id}/scale",
            json_body=req.model_dump(exclude_none=True),
        )
        return ScaleResponse(**data)

    async def scale_async(
        self,
        agent_id: str,
        direction: str,
        count: int = 1,
        reason: str | None = None,
    ) -> ScaleResponse:
        req = ScaleRequest(direction=ScaleDirection(direction), count=count, reason=reason)
        data = await self._client._request_async(
            "POST",
            f"{self._base}/agents/{agent_id}/scale",
            json_body=req.model_dump(exclude_none=True),
        )
        return ScaleResponse(**data)

    # ==================================================================
    # UPDATE (access settings)
    # ==================================================================

    @staticmethod
    def _update_body(access: AgentAccess | str | None, rate_limit_rpm: Any) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if access is not None:
            body["access"] = AgentAccess(access).value
        if rate_limit_rpm is not _UNSET:
            body["rate_limit_rpm"] = rate_limit_rpm
        return body

    def update(
        self,
        agent_id: str,
        *,
        access: AgentAccess | str | None = None,
        rate_limit_rpm: int | None = _UNSET,
    ) -> AgentResponse:
        """
        Change who may call an agent and how fast.

        Parameters
        ----------
        agent_id:
            Agent ID.
        access:
            ``"private"`` or ``"public"``. Omit to leave it unchanged.
        rate_limit_rpm:
            Requests per minute allowed from one client IP (1–100 000).
            Pass ``None`` to restore the platform default; omit to leave it
            unchanged.
        """
        data = self._client._request_sync(
            "PATCH",
            f"{self._base}/agents/{agent_id}",
            json_body=self._update_body(access, rate_limit_rpm),
        )
        return AgentResponse(**data)

    async def update_async(
        self,
        agent_id: str,
        *,
        access: AgentAccess | str | None = None,
        rate_limit_rpm: int | None = _UNSET,
    ) -> AgentResponse:
        data = await self._client._request_async(
            "PATCH",
            f"{self._base}/agents/{agent_id}",
            json_body=self._update_body(access, rate_limit_rpm),
        )
        return AgentResponse(**data)

    # ==================================================================
    # ACCESS KEYS (for private agents)
    # ==================================================================

    def create_access_key(self, agent_id: str, label: str = "default") -> AccessKeyCreated:
        """
        Create an access key for calling a private agent.

        The returned ``key`` (``agk_…``) is only ever returned here. It can
        call this one agent and nothing else, so it's safe to give to an app
        that calls the agent — unlike your account API key.
        """
        data = self._client._request_sync(
            "POST", f"{self._base}/agents/{agent_id}/access-keys", json_body={"label": label}
        )
        return AccessKeyCreated(**data)

    async def create_access_key_async(
        self, agent_id: str, label: str = "default"
    ) -> AccessKeyCreated:
        data = await self._client._request_async(
            "POST", f"{self._base}/agents/{agent_id}/access-keys", json_body={"label": label}
        )
        return AccessKeyCreated(**data)

    def list_access_keys(self, agent_id: str) -> builtins.list[AccessKey]:
        """List an agent's access keys (previews only, never the keys)."""
        data = self._client._request_sync("GET", f"{self._base}/agents/{agent_id}/access-keys")
        return [AccessKey(**k) for k in cast("builtins.list[dict[str, Any]]", data)]

    async def list_access_keys_async(self, agent_id: str) -> builtins.list[AccessKey]:
        data = await self._client._request_async(
            "GET", f"{self._base}/agents/{agent_id}/access-keys"
        )
        return [AccessKey(**k) for k in cast("builtins.list[dict[str, Any]]", data)]

    def delete_access_key(self, agent_id: str, key_id: str) -> None:
        """Revoke an access key; requests using it are refused from then on."""
        self._client._request_sync("DELETE", f"{self._base}/agents/{agent_id}/access-keys/{key_id}")

    async def delete_access_key_async(self, agent_id: str, key_id: str) -> None:
        await self._client._request_async(
            "DELETE", f"{self._base}/agents/{agent_id}/access-keys/{key_id}"
        )

    # ==================================================================
    # INVOKE (the agent's own URL, not the API)
    # ==================================================================

    @staticmethod
    def _invoke_url(agent: AgentResponse | str) -> str:
        if isinstance(agent, AgentResponse):
            if agent.url is None:
                raise AgntSparkError(f"Agent {agent.id} has no public URL.")
            base = str(agent.url)
        else:
            base = agent
        return base.rstrip("/") + "/invoke"

    @staticmethod
    def _invoke_request(
        message: str, session_id: str | None, access_key: str | None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        body: dict[str, Any] = {"input": message}
        if session_id is not None:
            body["session_id"] = session_id
        # Only the agent's own key: the account API key never goes to an agent.
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access_key is not None:
            headers["Authorization"] = f"Bearer {access_key}"
        return body, headers

    def invoke(
        self,
        agent: AgentResponse | str,
        message: str,
        *,
        session_id: str | None = None,
        access_key: str | None = None,
        timeout: float = 120.0,
    ) -> InvokeResponse:
        """
        Send a message to a running agent (``POST <agent url>/invoke``).

        Parameters
        ----------
        agent:
            The agent, its public URL, or its ID (which costs one API call to
            look up the URL).
        message:
            The user's message.
        session_id:
            Continue a conversation: pass the ``session_id`` of an earlier
            response.
        access_key:
            One of the agent's access keys (``agk_…``). Required for private
            agents. Your account API key is never sent to the agent.
        timeout:
            Seconds to wait; model calls with tools can take a while.

        Raises
        ------
        AuthenticationError
            The agent is private and ``access_key`` is missing or invalid.
        RateLimitError
            A rate limit was hit; see ``retry_after``.
        """
        if isinstance(agent, str) and not agent.startswith(("https://", "http://")):
            agent = self.get(agent)
        body, headers = self._invoke_request(message, session_id, access_key)
        response = httpx.post(self._invoke_url(agent), json=body, headers=headers, timeout=timeout)
        _raise_for_agent_response(response)
        return InvokeResponse(**response.json())

    async def invoke_async(
        self,
        agent: AgentResponse | str,
        message: str,
        *,
        session_id: str | None = None,
        access_key: str | None = None,
        timeout: float = 120.0,
    ) -> InvokeResponse:
        if isinstance(agent, str) and not agent.startswith(("https://", "http://")):
            agent = await self.get_async(agent)
        body, headers = self._invoke_request(message, session_id, access_key)
        async with httpx.AsyncClient(timeout=timeout) as http:
            response = await http.post(self._invoke_url(agent), json=body, headers=headers)
        _raise_for_agent_response(response)
        return InvokeResponse(**response.json())

    # ==================================================================
    # STREAMING (async only — SSE requires async I/O)
    # ==================================================================

    async def stream_logs(
        self,
        agent_id: str,
        *,
        level: str | None = None,
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
        params: dict[str, Any] = {}
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
