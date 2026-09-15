"""CLI commands for private agents: invoke, access and keys. The client is mocked."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from agntspark.cli import main
from agntspark.exceptions import AuthenticationError
from agntspark.models import AccessKey, AccessKeyCreated, AgentResponse, InvokeResponse

AGENT: dict[str, Any] = {
    "id": "agt_x",
    "name": "support",
    "runtime": "python3.12",
    "framework": "custom",
    "model": "gpt-4o",
    "status": "running",
    "created_at": "2026-09-15T00:00:00Z",
    "updated_at": "2026-09-15T00:00:00Z",
    "access": "private",
    "rate_limit_rpm": 30,
}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr("agntspark.cli._get_client", lambda ctx: client)
    return client


class TestInvoke:
    def test_prints_the_reply_verbatim(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.invoke.return_value = InvokeResponse(
            output="Use [bold] carefully", session_id="s1"
        )
        result = runner.invoke(main, ["invoke", "agt_x", "Hello", "--key", "agk_k"])

        assert result.exit_code == 0, result.output
        assert "Use [bold] carefully" in result.output
        assert "session: s1" in result.output
        mock_client.agents.invoke.assert_called_once_with(
            "agt_x", "Hello", session_id=None, access_key="agk_k"
        )

    def test_refusal_exits_non_zero(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.invoke.side_effect = AuthenticationError(
            "This agent is private.", status_code=401
        )
        result = runner.invoke(main, ["invoke", "agt_x", "Hello"])
        assert result.exit_code == 1
        assert "private" in result.output


class TestAccess:
    def test_sets_mode_and_limit(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.update.return_value = AgentResponse(**AGENT)
        result = runner.invoke(main, ["access", "agt_x", "private", "--rpm", "30"])

        assert result.exit_code == 0, result.output
        assert "private, 30 requests/min per caller" in result.output
        mock_client.agents.update.assert_called_once_with(
            "agt_x", access="private", rate_limit_rpm=30
        )

    def test_default_rpm_clears_the_limit(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.update.return_value = AgentResponse(**{**AGENT, "rate_limit_rpm": None})
        result = runner.invoke(main, ["access", "agt_x", "--default-rpm"])

        assert "default requests/min" in result.output
        mock_client.agents.update.assert_called_once_with("agt_x", access=None, rate_limit_rpm=None)

    def test_without_changes_it_only_reads(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.get.return_value = AgentResponse(**AGENT)
        runner.invoke(main, ["access", "agt_x"])
        mock_client.agents.update.assert_not_called()

    def test_conflicting_flags_are_rejected(
        self, runner: CliRunner, mock_client: MagicMock
    ) -> None:
        result = runner.invoke(main, ["access", "agt_x", "--rpm", "5", "--default-rpm"])
        assert result.exit_code == 2


class TestKeys:
    def test_create_shows_the_key_once(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.create_access_key.return_value = AccessKeyCreated(
            id="k1",
            label="web",
            key_preview="agk_abcdefgh...",
            created_at="2026-09-15T00:00:00Z",
            key="agk_the-whole-secret-key",
        )
        result = runner.invoke(main, ["keys", "create", "agt_x", "--label", "web"])

        assert result.exit_code == 0, result.output
        assert "agk_the-whole-secret-key" in result.output
        mock_client.agents.create_access_key.assert_called_once_with("agt_x", label="web")

    def test_list_and_revoke(self, runner: CliRunner, mock_client: MagicMock) -> None:
        mock_client.agents.list_access_keys.return_value = [
            AccessKey(
                id="k1", label="web", key_preview="agk_abcd...", created_at="2026-09-15T00:00:00Z"
            )
        ]
        listed = runner.invoke(main, ["keys", "list", "agt_x"])
        revoked = runner.invoke(main, ["keys", "revoke", "agt_x", "k1"])

        assert "agk_abcd..." in listed.output
        assert revoked.exit_code == 0
        mock_client.agents.delete_access_key.assert_called_once_with("agt_x", "k1")
