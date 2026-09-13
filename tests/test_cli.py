"""
Unit tests for the `agntspark` CLI's scale/delete subcommands.

The CLI previously had zero test coverage. Client is mocked (via
agntspark.cli._get_client) rather than hitting a real gateway.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from agntspark.cli import main
from agntspark.models import AgentStatus, ScaleDirection, ScaleResponse


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mock_client(monkeypatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr("agntspark.cli._get_client", lambda ctx: client)
    return client


class TestScale:
    def test_scale_up_calls_client_and_prints_result(
        self, runner: CliRunner, mock_client: MagicMock
    ) -> None:
        mock_client.agents.scale.return_value = ScaleResponse(
            agent_id="agt_x",
            previous_replicas=1,
            current_replicas=2,
            direction=ScaleDirection.UP,
            status=AgentStatus.RUNNING,
        )

        result = runner.invoke(main, ["scale", "agt_x", "up", "--count", "1"])

        assert result.exit_code == 0
        mock_client.agents.scale.assert_called_once_with("agt_x", "up", count=1, reason=None)
        assert "agt_x" in result.output
        assert "2" in result.output  # current_replicas

    def test_scale_rejects_invalid_direction(
        self, runner: CliRunner, mock_client: MagicMock
    ) -> None:
        result = runner.invoke(main, ["scale", "agt_x", "sideways"])
        assert result.exit_code != 0
        mock_client.agents.scale.assert_not_called()

    def test_scale_passes_reason(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.scale.return_value = ScaleResponse(
            agent_id="agt_x",
            previous_replicas=2,
            current_replicas=1,
            direction=ScaleDirection.DOWN,
            status=AgentStatus.RUNNING,
        )
        result = runner.invoke(main, ["scale", "agt_x", "down", "--reason", "cost cutting"])
        assert result.exit_code == 0
        mock_client.agents.scale.assert_called_once_with(
            "agt_x", "down", count=1, reason="cost cutting"
        )


class TestDelete:
    def test_delete_with_yes_flag_skips_prompt(
        self, runner: CliRunner, mock_client: MagicMock
    ) -> None:
        result = runner.invoke(main, ["delete", "agt_x", "--yes"])
        assert result.exit_code == 0
        mock_client.agents.delete.assert_called_once_with("agt_x")
        assert "deleted" in result.output.lower()

    def test_delete_without_yes_prompts_and_confirms(
        self, runner: CliRunner, mock_client: MagicMock
    ) -> None:
        result = runner.invoke(main, ["delete", "agt_x"], input="y\n")
        assert result.exit_code == 0
        mock_client.agents.delete.assert_called_once_with("agt_x")

    def test_delete_aborts_when_not_confirmed(
        self, runner: CliRunner, mock_client: MagicMock
    ) -> None:
        result = runner.invoke(main, ["delete", "agt_x"], input="n\n")
        assert result.exit_code == 0
        mock_client.agents.delete.assert_not_called()
        assert "aborted" in result.output.lower()
