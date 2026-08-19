"""
Pydantic data models for the AgntSpark SDK.

These models mirror the JSON schemas returned by the AgntSpark REST API
(v1).  They provide type-safe access to agent configurations, deployment
metadata, runtime metrics, and log entries.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AgentStatus(str, Enum):
    """Lifecycle status of an agent on the AgntSpark platform."""

    PENDING = "pending"
    BUILDING = "building"
    STARTING = "starting"
    RUNNING = "running"
    SCALING = "scaling"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    CRASHED = "crashed"

    @property
    def is_healthy(self) -> bool:
        """Return ``True`` when the status indicates the agent is reachable."""
        return self in (AgentStatus.RUNNING, AgentStatus.STARTING, AgentStatus.SCALING)


class AgentRuntime(str, Enum):
    """Supported execution runtimes for agent containers."""

    PYTHON_3_12 = "python3.12"
    PYTHON_3_11 = "python3.11"
    PYTHON_3_10 = "python3.10"
    NODE_20 = "node20"
    NODE_22 = "node22"
    CUSTOM = "custom"


class ScaleDirection(str, Enum):
    """Direction of a manual scaling operation."""

    UP = "up"
    DOWN = "down"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ResourceLimits(BaseModel):
    """
    Compute resource quotas applied to a single agent instance.

    All values are per-replica — the platform multiplies them by the
    current replica count to derive total usage.
    """

    cpu: float = Field(
        default=1.0,
        ge=0.1,
        le=64.0,
        description="CPU cores allocated per replica (0.1–64).",
    )
    memory_mb: int = Field(
        default=512,
        ge=128,
        le=65536,
        description="Memory in megabytes per replica (128–65 536).",
    )
    gpu: int = Field(
        default=0,
        ge=0,
        le=8,
        description="Number of GPUs allocated per replica.",
    )
    gpu_type: Optional[str] = Field(
        default=None,
        description="GPU model identifier (e.g. ``nvidia-a10g``). Required when ``gpu > 0``.",
    )
    disk_gb: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Ephemeral disk storage in gigabytes.",
    )
    ephemeral_storage_gb: int = Field(
        default=5,
        ge=1,
        le=500,
        description="Ephemeral storage for scratch space.",
    )

    @field_validator("gpu_type")
    @classmethod
    def _validate_gpu_type(cls, v: Optional[str], info: Any) -> Optional[str]:
        values = info.data
        if values.get("gpu", 0) > 0 and not v:
            raise ValueError("gpu_type must be specified when gpu > 0")
        return v


class EnvVar(BaseModel):
    """A single environment variable injected into the agent container."""

    key: str = Field(..., min_length=1, max_length=256)
    value: str = Field(default="", max_length=4096)
    secret: bool = Field(
        default=False,
        description="When ``True`` the value is stored encrypted and redacted in API responses.",
    )


class DeployConfig(BaseModel):
    """
    Deployment configuration submitted to :meth:`AgntSparkClient.deploy`.

    Either ``image`` or ``build_path`` must be provided.  If both are set,
    ``image`` takes precedence and ``build_path`` is ignored.
    """

    replicas: int = Field(default=1, ge=1, le=100, description="Number of initial replicas.")
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    env: List[EnvVar] = Field(
        default_factory=list,
        description="Environment variables to inject into the agent container.",
    )
    image: Optional[str] = Field(
        default=None,
        description="Pre-built container image reference (e.g. ``ghcr.io/myorg/agent:1.0``).",
    )
    build_path: Optional[str] = Field(
        default=None,
        description="Path to a build context directory or Git URL.  Mutually exclusive with ``image``.",
    )
    command: Optional[str] = Field(
        default=None,
        description="Override the container ENTRYPOINT.",
    )
    args: List[str] = Field(
        default_factory=list,
        description="Arguments appended to the container CMD.",
    )
    health_check_path: Optional[str] = Field(
        default=None,
        description="HTTP path (``/health``) polled by the platform to determine readiness.",
    )
    auto_scale: bool = Field(
        default=False,
        description="Enable horizontal pod autoscaling based on CPU/memory thresholds.",
    )
    min_replicas: int = Field(default=1, ge=1, le=100)
    max_replicas: int = Field(default=10, ge=1, le=100)
    port: int = Field(default=8080, ge=1, le=65535, description="Container port exposed to the platform ingress.")

    @field_validator("max_replicas")
    @classmethod
    def _max_ge_min(cls, v: int, info: Any) -> int:
        min_r = info.data.get("min_replicas", 1)
        if v < min_r:
            raise ValueError("max_replicas must be >= min_replicas")
        return v

    @field_validator("build_path")
    @classmethod
    def _image_or_path(cls, v: Optional[str], info: Any) -> Optional[str]:
        image = info.data.get("image")
        if not image and not v:
            raise ValueError("Either 'image' or 'build_path' must be provided")
        return v


# ---------------------------------------------------------------------------
# Top-level request/response models
# ---------------------------------------------------------------------------

class AgentConfig(BaseModel):
    """
    Configuration for creating a new agent.

    This is the primary input to :meth:`AgntSparkClient.agents.create`.
    """

    name: str = Field(..., min_length=1, max_length=128, description="Human-readable agent name.")
    runtime: Optional[AgentRuntime] = Field(
        default=AgentRuntime.PYTHON_3_12,
        description="Execution runtime for the agent container.",
    )
    framework: str = Field(
        default="custom",
        description="Agent framework identifier (``langchain``, ``autogen``, ``crewai``, ``custom``).",
    )
    model: str = Field(
        default="gpt-4o",
        description="Underlying LLM model identifier.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the model provider.  If omitted, the platform-level key is used.",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        max_length=32_000,
        description="System prompt injected into every conversation.",
    )
    deploy: Optional[DeployConfig] = Field(
        default=None,
        description="Optional deployment configuration.  If provided, the agent is deployed immediately after creation.",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Arbitrary tags for grouping and filtering agents.",
    )
    metadata: Dict[str, str] = Field(
        default_factory=dict,
        description="Free-form key/value metadata stored alongside the agent.",
    )

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: List[str]) -> List[str]:
        for tag in v:
            if len(tag) > 64:
                raise ValueError(f"Tag '{tag[:20]}…' exceeds 64 characters")
        return v


class AgentResponse(BaseModel):
    """
    Full representation of an agent as returned by the AgntSpark API.

    This model is produced by ``get``, ``list``, ``create``, and ``deploy``
    responses.
    """

    id: str = Field(..., description="Unique agent identifier (``agt_…``).")
    name: str
    runtime: AgentRuntime
    framework: str
    model: str
    status: AgentStatus
    created_at: datetime
    updated_at: datetime
    url: Optional[HttpUrl] = Field(default=None, description="Public URL of a running agent.")
    deploy: Optional[DeployConfig] = Field(default=None)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None, description="Error message if the agent is in a failed state.")
    version: int = Field(default=1, description="Monotonically increasing version number.")
    replicas: int = Field(default=0, description="Current number of running replicas.")


class Metrics(BaseModel):
    """
    Aggregated runtime metrics for an agent over a specified time window.

    All numeric fields are averages over the queried period unless
    otherwise noted.
    """

    agent_id: str
    timestamp: datetime
    cpu_percent: float = Field(..., ge=0.0, description="Average CPU utilisation (%).")
    memory_mb: int = Field(..., ge=0, description="Average memory usage in MB.")
    memory_percent: float = Field(..., ge=0.0, le=100.0)
    gpu_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    gpu_memory_mb: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0, description="Total requests in the window.")
    request_rate: float = Field(default=0.0, ge=0.0, description="Requests per second.")
    error_count: int = Field(default=0, ge=0)
    error_rate: float = Field(default=0.0, ge=0.0)
    p50_latency_ms: float = Field(default=0.0, ge=0.0, description="Median request latency in milliseconds.")
    p95_latency_ms: float = Field(default=0.0, ge=0.0)
    p99_latency_ms: float = Field(default=0.0, ge=0.0)
    replicas: int = Field(default=0, ge=0)


class AgentLog(BaseModel):
    """A single log line emitted by an agent instance."""

    agent_id: str
    replica_id: str = Field(..., description="Identifier of the replica that produced the log.")
    timestamp: datetime
    level: str = Field(..., description="Log level (``DEBUG``, ``INFO``, ``WARN``, ``ERROR``).")
    message: str
    source: str = Field(default="stdout", description="Log source (``stdout``, ``stderr``, ``platform``).")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScaleRequest(BaseModel):
    """Request body for the ``scale`` endpoint."""

    direction: ScaleDirection
    count: int = Field(default=1, ge=1, le=50, description="Number of replicas to add or remove.")
    reason: Optional[str] = Field(default=None, max_length=500)


class ScaleResponse(BaseModel):
    """Result of a scaling operation."""

    agent_id: str
    previous_replicas: int
    current_replicas: int
    direction: ScaleDirection
    status: AgentStatus


# ---------------------------------------------------------------------------
# Collection responses
# ---------------------------------------------------------------------------

class AgentListResponse(BaseModel):
    """Paginated agent list response."""

    agents: List[AgentResponse]
    total: int = Field(..., ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    has_next: bool = Field(default=False)


class LogListResponse(BaseModel):
    """Paginated log response."""

    logs: List[AgentLog]
    total: int = Field(..., ge=0)
    has_next: bool = Field(default=False)
    next_cursor: Optional[str] = None


__all__ = [
    "AgentConfig",
    "AgentResponse",
    "AgentRuntime",
    "AgentStatus",
    "ScaleDirection",
    "ResourceLimits",
    "EnvVar",
    "DeployConfig",
    "Metrics",
    "AgentLog",
    "ScaleRequest",
    "ScaleResponse",
    "AgentListResponse",
    "LogListResponse",
]
