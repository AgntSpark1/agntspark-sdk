"""
Unit tests for AgntSpark SDK data models.

Run::

    pytest tests/test_models.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from agntspark.models import (
    AgentConfig,
    AgentListResponse,
    AgentLog,
    AgentResponse,
    AgentRuntime,
    AgentStatus,
    DeployConfig,
    EnvVar,
    LogListResponse,
    Metrics,
    ResourceLimits,
    ScaleDirection,
    ScaleRequest,
    ScaleResponse,
)

# ---------------------------------------------------------------------------
# ResourceLimits
# ---------------------------------------------------------------------------


class TestResourceLimits:
    def test_defaults(self) -> None:
        rl = ResourceLimits()
        assert rl.cpu == 1.0
        assert rl.memory_mb == 512
        assert rl.gpu == 0
        assert rl.disk_gb == 10

    def test_cpu_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ResourceLimits(cpu=0.05)
        with pytest.raises(ValidationError):
            ResourceLimits(cpu=100.0)

    def test_gpu_type_required_when_gpu_positive(self) -> None:
        with pytest.raises(ValidationError):
            ResourceLimits(gpu=1, gpu_type=None)

    def test_gpu_type_accepted(self) -> None:
        rl = ResourceLimits(gpu=2, gpu_type="nvidia-a10g")
        assert rl.gpu_type == "nvidia-a10g"

    def test_memory_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ResourceLimits(memory_mb=32)
        with pytest.raises(ValidationError):
            ResourceLimits(memory_mb=100_000)


# ---------------------------------------------------------------------------
# DeployConfig
# ---------------------------------------------------------------------------


class TestDeployConfig:
    def test_requires_image_or_build_path(self) -> None:
        with pytest.raises(ValidationError):
            DeployConfig()

    def test_image_only(self) -> None:
        dc = DeployConfig(image="ghcr.io/myorg/agent:1.0")
        assert dc.image == "ghcr.io/myorg/agent:1.0"
        assert dc.build_path is None

    def test_build_path_only(self) -> None:
        dc = DeployConfig(build_path="./agent")
        assert dc.build_path == "./agent"

    def test_max_replicas_ge_min(self) -> None:
        with pytest.raises(ValidationError):
            DeployConfig(image="test:1.0", min_replicas=5, max_replicas=2)

    def test_replicas_bounds(self) -> None:
        dc = DeployConfig(image="test:1.0", replicas=10)
        assert dc.replicas == 10
        with pytest.raises(ValidationError):
            DeployConfig(image="test:1.0", replicas=0)

    def test_env_vars(self) -> None:
        dc = DeployConfig(
            image="test:1.0",
            env=[EnvVar(key="OPENAI_API_KEY", value="sk-…", secret=True)],
        )
        assert dc.env[0].secret is True

    def test_auto_scale(self) -> None:
        dc = DeployConfig(image="test:1.0", auto_scale=True, min_replicas=2, max_replicas=20)
        assert dc.auto_scale is True
        assert dc.min_replicas == 2


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------


class TestAgentConfig:
    def test_minimal(self) -> None:
        ac = AgentConfig(name="test-bot")
        assert ac.name == "test-bot"
        assert ac.runtime == AgentRuntime.PYTHON_3_12
        assert ac.framework == "custom"
        assert ac.model == "gpt-4o"

    def test_name_too_long(self) -> None:
        with pytest.raises(ValidationError):
            AgentConfig(name="x" * 129)

    def test_name_empty(self) -> None:
        with pytest.raises(ValidationError):
            AgentConfig(name="")

    def test_tags_validation(self) -> None:
        ac = AgentConfig(name="bot", tags=["prod", "v2"])
        assert ac.tags == ["prod", "v2"]

    def test_tag_too_long(self) -> None:
        with pytest.raises(ValidationError):
            AgentConfig(name="bot", tags=["x" * 65])

    def test_with_deploy_config(self) -> None:
        ac = AgentConfig(
            name="bot",
            runtime=AgentRuntime.PYTHON_3_11,
            deploy=DeployConfig(image="test:1.0", replicas=3),
        )
        assert ac.deploy is not None
        assert ac.deploy.replicas == 3
        assert ac.runtime == AgentRuntime.PYTHON_3_11


# ---------------------------------------------------------------------------
# AgentResponse
# ---------------------------------------------------------------------------


class TestAgentResponse:
    def _make(self, **overrides: Any) -> AgentResponse:
        defaults = {
            "id": "agt_abc123",
            "name": "test-agent",
            "runtime": AgentRuntime.PYTHON_3_12,
            "framework": "langchain",
            "model": "gpt-4o",
            "status": AgentStatus.RUNNING,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
        defaults.update(overrides)
        return AgentResponse(**defaults)

    def test_basic(self) -> None:
        ar = self._make()
        assert ar.id == "agt_abc123"
        assert ar.version == 1
        assert ar.replicas == 0

    def test_status_is_healthy(self) -> None:
        assert AgentStatus.RUNNING.is_healthy
        assert AgentStatus.STARTING.is_healthy
        assert not AgentStatus.STOPPED.is_healthy
        assert not AgentStatus.FAILED.is_healthy

    def test_url_optional(self) -> None:
        ar = self._make()
        assert ar.url is None

    def test_error_optional(self) -> None:
        ar = self._make(status=AgentStatus.FAILED, error="OOM killed")
        assert ar.error == "OOM killed"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_valid(self) -> None:
        m = Metrics(
            agent_id="agt_abc123",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            cpu_percent=45.5,
            memory_mb=1024,
            memory_percent=50.0,
            request_count=1000,
            request_rate=16.7,
            p50_latency_ms=120.0,
            p95_latency_ms=250.0,
            p99_latency_ms=500.0,
        )
        assert m.cpu_percent == 45.5
        assert m.gpu_percent == 0.0

    def test_memory_percent_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Metrics(
                agent_id="agt_x",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                cpu_percent=50,
                memory_mb=100,
                memory_percent=150.0,
            )


# ---------------------------------------------------------------------------
# AgentLog
# ---------------------------------------------------------------------------


class TestAgentLog:
    def test_basic(self) -> None:
        log = AgentLog(
            agent_id="agt_abc123",
            replica_id="rpt_001",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            level="INFO",
            message="Agent started successfully",
        )
        assert log.source == "stdout"
        assert log.level == "INFO"


# ---------------------------------------------------------------------------
# Scale models
# ---------------------------------------------------------------------------


class TestScaleModels:
    def test_scale_request(self) -> None:
        sr = ScaleRequest(direction=ScaleDirection.UP, count=3)
        assert sr.direction == ScaleDirection.UP
        assert sr.count == 3
        assert sr.reason is None

    def test_scale_count_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ScaleRequest(direction=ScaleDirection.UP, count=0)

    def test_scale_response(self) -> None:
        resp = ScaleResponse(
            agent_id="agt_x",
            previous_replicas=2,
            current_replicas=5,
            direction=ScaleDirection.UP,
            status=AgentStatus.SCALING,
        )
        assert resp.current_replicas == 5


# ---------------------------------------------------------------------------
# Collection responses
# ---------------------------------------------------------------------------


class TestCollectionResponses:
    def _make_agent(self, **kw: Any) -> AgentResponse:
        defaults = {
            "id": "agt_1",
            "name": "a",
            "runtime": AgentRuntime.PYTHON_3_12,
            "framework": "custom",
            "model": "gpt-4o",
            "status": AgentStatus.RUNNING,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        defaults.update(kw)
        return AgentResponse(**defaults)

    def test_agent_list(self) -> None:
        resp = AgentListResponse(
            agents=[self._make_agent(), self._make_agent(id="agt_2")],
            total=2,
        )
        assert len(resp.agents) == 2
        assert resp.total == 2
        assert resp.has_next is False

    def test_log_list(self) -> None:
        resp = LogListResponse(
            logs=[
                AgentLog(
                    agent_id="agt_x",
                    replica_id="r1",
                    timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    level="INFO",
                    message="hello",
                )
            ],
            total=1,
        )
        assert len(resp.logs) == 1
        assert resp.next_cursor is None
