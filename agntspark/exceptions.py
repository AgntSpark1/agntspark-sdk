"""
Custom exception hierarchy for the AgntSpark SDK.

All exceptions raised by the SDK inherit from :class:`AgntSparkError`,
so callers can catch any SDK-related error with a single ``except`` clause
when desired.
"""

from __future__ import annotations

from typing import Any, Optional


class AgntSparkError(Exception):
    """
    Base exception for every error raised by the AgntSpark SDK.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    status_code:
        HTTP status code from the API response, if applicable.
    response_body:
        Raw response body from the API, if available.  Useful for debugging.
    """

    def __init__(
        self,
        message: str = "An unspecified AgntSpark SDK error occurred.",
        *,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        if self.status_code is not None:
            return f"[{self.status_code}] {self.message}"
        return self.message


class AuthenticationError(AgntSparkError):
    """
    Raised when the API key is missing, invalid, or expired (HTTP 401/403).
    """

    def __init__(
        self,
        message: str = "Authentication failed.",
        *,
        status_code: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, status_code=status_code or 401, **{k: v for k, v in kwargs.items() if k != "status_code"})


class RateLimitError(AgntSparkError):
    """
    Raised when the AgntSpark API rate limit is exceeded (HTTP 429).

    The ``retry_after`` attribute (in seconds) is populated from the
    ``Retry-After`` response header when available.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded.",
        *,
        retry_after: Optional[float] = None,
        status_code: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, status_code=status_code or 429, **{k: v for k, v in kwargs.items() if k != "status_code"})
        self.retry_after = retry_after


class NotFoundError(AgntSparkError):
    """
    Raised when a requested resource (agent, deployment, log stream) does not
    exist (HTTP 404).
    """

    def __init__(self, resource: str = "Resource", *, status_code: Optional[int] = None, **kwargs: Any) -> None:
        message = f"{resource} not found."
        super().__init__(message, status_code=status_code or 404, **{k: v for k, v in kwargs.items() if k != "status_code"})


class DeploymentError(AgntSparkError):
    """
    Raised when an agent deployment fails on the server side.

    This covers build errors, resource-allocation failures, image pull
    errors, and any other non-HTTP-level deployment problems.
    """

    def __init__(
        self,
        message: str = "Agent deployment failed.",
        *,
        agent_id: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, status_code=status_code or 502, **{k: v for k, v in kwargs.items() if k != "status_code"})
        self.agent_id = agent_id


__all__ = [
    "AgntSparkError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "DeploymentError",
]
