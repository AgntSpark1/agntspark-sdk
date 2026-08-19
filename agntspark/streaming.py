"""
Server-Sent Events (SSE) streaming utilities for the AgntSpark SDK.

The streaming module provides a thin, fully-typed wrapper around the
AgntSpark SSE endpoints.  It handles reconnection, backpressure, and
JSON decoding so that callers can focus on the data.

Two stream types are supported:

* **Log streams** — real-time stdout/stderr from agent replicas.
* **Metric streams** — periodic CPU/memory/latency telemetry.

Example::

    from agntspark import Client
    from agntspark.streaming import StreamEvent

    async with Client(api_key="sk-…") as client:
        async for event in client.agents.stream_logs("agt_abc123"):
            if event.type == "log":
                print(f"[{event.data['level']}] {event.data['message']}")
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, Optional

import httpx

logger = logging.getLogger("agntspark.streaming")


class EventType(str, Enum):
    """Type of SSE event emitted by the AgntSpark platform."""

    LOG = "log"
    METRIC = "metric"
    STATUS = "status"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass
class StreamEvent:
    """
    A single decoded SSE event.

    Attributes
    ----------
    type:
        The event type (``log``, ``metric``, ``status``, …).
    data:
        Decoded JSON payload from the event ``data:`` field.
    id:
        SSE event ID, used for resumption via ``Last-Event-ID``.
    retry:
        Reconnection interval in milliseconds suggested by the server.
    raw:
        The raw ``data:`` string before JSON decoding (for debugging).
    """

    type: EventType = EventType.HEARTBEAT
    data: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    retry: Optional[int] = None
    raw: str = ""


class SSEStream:
    """
    Low-level SSE client that yields :class:`StreamEvent` objects.

    This class handles:

    * Connection establishment with authentication headers.
    * Line-by-line parsing of the SSE wire format.
    * Automatic reconnection with exponential backoff.
    * Graceful shutdown when the context manager exits.

    Users typically do not construct :class:`SSEStream` directly; it is
    created by :meth:`agntspark.client.Agents.stream_logs` and
    :meth:`agntspark.client.Agents.stream_metrics`.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        *,
        reconnect: bool = True,
        max_reconnects: int = 5,
        reconnect_delay: float = 1.0,
        reconnect_backoff: float = 2.0,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._reconnect = reconnect
        self._max_reconnects = max_reconnects
        self._reconnect_delay = reconnect_delay
        self._reconnect_backoff = reconnect_backoff
        self._client: Optional[httpx.AsyncClient] = None
        self._response: Optional[httpx.Response] = None
        self._closed = False

    async def __aenter__(self) -> "SSEStream":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP connection."""
        self._closed = True
        if self._response is not None:
            await self._response.aclose()
            self._response = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def events(self) -> AsyncIterator[StreamEvent]:
        """
        Asynchronously iterate over SSE events.

        The iterator blocks until the stream is closed by the server
        or :meth:`close` is called.  If reconnection is enabled and
        the connection drops, the stream will attempt to reconnect
        up to ``max_reconnects`` times before giving up.
        """
        reconnect_attempts = 0

        while not self._closed:
            try:
                async for event in self._iter_events():
                    yield event
                # Server closed the connection cleanly
                if self._reconnect and reconnect_attempts < self._max_reconnects:
                    reconnect_attempts += 1
                    delay = self._reconnect_delay * (
                        self._reconnect_backoff ** (reconnect_attempts - 1)
                    )
                    logger.warning(
                        "SSE stream closed; reconnecting in %.1fs (attempt %d/%d)",
                        delay,
                        reconnect_attempts,
                        self._max_reconnects,
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except httpx.HTTPStatusError as exc:
                logger.error("SSE HTTP error: %s", exc)
                raise
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                if not self._reconnect or reconnect_attempts >= self._max_reconnects:
                    raise
                reconnect_attempts += 1
                delay = self._reconnect_delay * (
                    self._reconnect_backoff ** (reconnect_attempts - 1)
                )
                logger.warning(
                    "SSE connection error (%s); reconnecting in %.1fs (attempt %d/%d)",
                    type(exc).__name__,
                    delay,
                    reconnect_attempts,
                    self._max_reconnects,
                )
                await asyncio.sleep(delay)

    async def _iter_events(self) -> AsyncIterator[StreamEvent]:
        """Iterate over events from a single SSE connection."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

        # Last-Event-ID header for resumption
        last_event_id: Optional[str] = None
        headers = dict(self._headers)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id

        async with self._client.stream("GET", self._url, headers=headers) as response:
            self._response = response
            response.raise_for_status()

            event_type: str = "message"
            data_lines: list[str] = []
            event_id: Optional[str] = None
            retry_ms: Optional[int] = None

            async for line in response.aiter_lines():
                if self._closed:
                    break

                # SSE spec: lines starting with ":" are comments (heartbeats)
                if line.startswith(":"):
                    yield StreamEvent(type=EventType.HEARTBEAT, data={"comment": line[1:].strip()})
                    continue

                # Empty line = event boundary
                if line == "":
                    if data_lines:
                        raw_data = "\n".join(data_lines)
                        try:
                            parsed = json.loads(raw_data)
                        except json.JSONDecodeError:
                            parsed = {"raw": raw_data}

                        try:
                            etype = EventType(event_type)
                        except ValueError:
                            etype = EventType.STATUS

                        if event_id:
                            last_event_id = event_id

                        yield StreamEvent(
                            type=etype,
                            data=parsed,
                            id=event_id,
                            retry=retry_ms,
                            raw=raw_data,
                        )

                    # Reset accumulators
                    event_type = "message"
                    data_lines = []
                    event_id = None
                    retry_ms = None
                    continue

                # Parse field:value
                if ":" not in line:
                    # Malformed line — skip silently per SSE spec
                    continue

                field_name, _, field_value = line.partition(":")

                # Per spec, a leading space after ":" is optional and stripped
                if field_value.startswith(" "):
                    field_value = field_value[1:]

                if field_name == "event":
                    event_type = field_value
                elif field_name == "data":
                    data_lines.append(field_value)
                elif field_name == "id":
                    event_id = field_value
                elif field_name == "retry":
                    try:
                        retry_ms = int(field_value)
                    except ValueError:
                        pass


def parse_sse_line(line: str) -> Optional[tuple[str, str]]:
    """
    Parse a single SSE wire line into ``(field_name, value)``.

    Returns ``None`` for comment lines or malformed input.
    """
    if line.startswith(":") or ":" not in line:
        return None
    name, _, value = line.partition(":")
    if value.startswith(" "):
        value = value[1:]
    return name, value


__all__ = ["SSEStream", "StreamEvent", "EventType", "parse_sse_line"]
