"""Private agents: access settings, access keys, and invoking an agent's URL."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from agntspark import (
    AgentAccess,
    AgntSparkError,
    AuthenticationError,
    Client,
    RateLimitError,
)

BASE_URL = "https://agntapi.agntspark.com/v1"
AGENT_URL = "https://support-k3v9qa.run.agntspark.com"

AGENT: dict[str, Any] = {
    "id": "agt_abc123",
    "name": "support",
    "runtime": "python3.12",
    "framework": "custom",
    "model": "gpt-4o",
    "status": "running",
    "created_at": "2026-09-15T00:00:00Z",
    "updated_at": "2026-09-15T00:00:00Z",
    "url": AGENT_URL,
    "replicas": 1,
    "access": "private",
    "rate_limit_rpm": None,
}

KEY: dict[str, Any] = {
    "id": "7d1c8e2a-0000-4000-8000-000000000001",
    "label": "website",
    "key_preview": "agk_Xy12abcd...",
    "created_at": "2026-09-15T00:00:00Z",
}


def _client() -> Client:
    return Client(api_key="agnt_account-key", base_url=BASE_URL, max_retries=0)


def _json(request: httpx.Request) -> Any:
    return json.loads(request.content)


class TestCreate:
    @respx.mock
    def test_private_agents_come_with_their_first_key(self) -> None:
        route = respx.post(f"{BASE_URL}/agents").mock(
            return_value=httpx.Response(201, json={**AGENT, "access_key": "agk_first"})
        )
        with _client() as client:
            agent = client.agents.create("support", api_key="sk-model", access="private")

        assert _json(route.calls.last.request)["access"] == "private"
        assert _json(route.calls.last.request)["api_key"] == "sk-model"
        assert agent.access is AgentAccess.PRIVATE
        assert agent.access_key == "agk_first"

    @respx.mock
    def test_access_is_left_to_the_platform_when_omitted(self) -> None:
        route = respx.post(f"{BASE_URL}/agents").mock(return_value=httpx.Response(201, json=AGENT))
        with _client() as client:
            client.agents.create("support")
        assert "access" not in _json(route.calls.last.request)


class TestUpdate:
    @respx.mock
    def test_omitted_fields_are_not_sent_and_none_clears_the_limit(self) -> None:
        route = respx.patch(f"{BASE_URL}/agents/agt_abc123").mock(
            return_value=httpx.Response(200, json=AGENT)
        )
        with _client() as client:
            client.agents.update("agt_abc123", access=AgentAccess.PUBLIC)
            client.agents.update("agt_abc123", rate_limit_rpm=30)
            client.agents.update("agt_abc123", rate_limit_rpm=None)

        bodies = [_json(call.request) for call in route.calls]
        assert bodies == [{"access": "public"}, {"rate_limit_rpm": 30}, {"rate_limit_rpm": None}]

    @respx.mock
    async def test_async(self) -> None:
        route = respx.patch(f"{BASE_URL}/agents/agt_abc123").mock(
            return_value=httpx.Response(200, json=AGENT)
        )
        async with _client() as client:
            agent = await client.agents.update_async("agt_abc123", access="private")
        assert _json(route.calls.last.request) == {"access": "private"}
        assert agent.access is AgentAccess.PRIVATE


class TestAccessKeys:
    @respx.mock
    def test_create_list_delete(self) -> None:
        create = respx.post(f"{BASE_URL}/agents/agt_abc123/access-keys").mock(
            return_value=httpx.Response(201, json={**KEY, "key": "agk_secret"})
        )
        respx.get(f"{BASE_URL}/agents/agt_abc123/access-keys").mock(
            return_value=httpx.Response(200, json=[KEY])
        )
        delete = respx.delete(f"{BASE_URL}/agents/agt_abc123/access-keys/{KEY['id']}").mock(
            return_value=httpx.Response(204)
        )

        with _client() as client:
            created = client.agents.create_access_key("agt_abc123", label="website")
            listed = client.agents.list_access_keys("agt_abc123")
            client.agents.delete_access_key("agt_abc123", KEY["id"])

        assert _json(create.calls.last.request) == {"label": "website"}
        assert created.key == "agk_secret"
        assert [k.key_preview for k in listed] == ["agk_Xy12abcd..."]
        assert not hasattr(listed[0], "key")
        assert delete.called

    @respx.mock
    async def test_async_list(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_abc123/access-keys").mock(
            return_value=httpx.Response(200, json=[KEY])
        )
        async with _client() as client:
            listed = await client.agents.list_access_keys_async("agt_abc123")
        assert listed[0].label == "website"


class TestInvoke:
    @respx.mock
    def test_sends_only_the_access_key_to_the_agent(self) -> None:
        route = respx.post(f"{AGENT_URL}/invoke").mock(
            return_value=httpx.Response(
                200, json={"output": "Hi!", "session_id": "s1", "agent_id": "agt_abc123"}
            )
        )
        with _client() as client:
            reply = client.agents.invoke(AGENT_URL, "Hello", session_id="s0", access_key="agk_k")

        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer agk_k"
        assert "agnt_account-key" not in str(request.headers)
        assert _json(request) == {"input": "Hello", "session_id": "s0"}
        assert (reply.output, reply.session_id) == ("Hi!", "s1")

    @respx.mock
    def test_an_agent_id_is_resolved_to_its_url(self) -> None:
        respx.get(f"{BASE_URL}/agents/agt_abc123").mock(
            return_value=httpx.Response(200, json=AGENT)
        )
        route = respx.post(f"{AGENT_URL}/invoke").mock(
            return_value=httpx.Response(200, json={"output": "ok", "session_id": "s"})
        )
        with _client() as client:
            client.agents.invoke("agt_abc123", "Hello", access_key="agk_k")
        assert "Authorization" in route.calls.last.request.headers

    @respx.mock
    async def test_async_with_an_agent_object(self) -> None:
        respx.post(f"{AGENT_URL}/invoke").mock(
            return_value=httpx.Response(200, json={"output": "async ok", "session_id": "s"})
        )
        async with _client() as client:
            agent = client.agents._client  # noqa: F841 — keep the client open
            from agntspark import AgentResponse

            reply = await client.agents.invoke_async(AgentResponse(**AGENT), "Hello")
        assert reply.output == "async ok"

    @respx.mock
    def test_edge_refusals_map_to_sdk_errors(self) -> None:
        respx.post(f"{AGENT_URL}/invoke").mock(
            side_effect=[
                httpx.Response(401, text="This agent is private.\n"),
                httpx.Response(429, text="Too many requests.\n", headers={"Retry-After": "12"}),
                httpx.Response(
                    400, json={"error": {"code": "INVALID_REQUEST", "message": "input: required"}}
                ),
            ]
        )
        with _client() as client:
            with pytest.raises(AuthenticationError, match="private"):
                client.agents.invoke(AGENT_URL, "Hello")
            with pytest.raises(RateLimitError) as limited:
                client.agents.invoke(AGENT_URL, "Hello")
            with pytest.raises(AgntSparkError, match="input: required"):
                client.agents.invoke(AGENT_URL, "Hello")
        assert limited.value.retry_after == 12.0

    def test_agent_without_url_is_refused_before_any_request(self) -> None:
        from agntspark import AgentResponse

        with _client() as client, pytest.raises(AgntSparkError, match="no public URL"):
            client.agents.invoke(AgentResponse(**{**AGENT, "url": None}), "Hello")
