import json

import httpx
import pytest

from valheim_ai.protocol import Snapshot
from valheim_ai.providers import ProviderError, decide


VALID = {"action": "follow", "target_id": "leader", "duration": 2, "say": "Следую"}


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_provider_request_and_response(provider, snapshot):
    def handle(request):
        body = json.loads(request.content)
        assert "keep-up" in request.content.decode()
        if provider == "openai":
            assert str(request.url) == "https://api.openai.com/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-secret"
            assert body["response_format"]["json_schema"]["strict"] is True
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})
        assert request.headers["x-goog-api-key"] == "test-secret"
        assert "key=" not in str(request.url)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"thought": True, "text": "do not parse hidden thoughts"}, {"text": json.dumps(VALID)}
        ]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        d = await decide(client, provider, "test-model", "test-secret", "keep-up", Snapshot(**snapshot))
    assert d.action == "follow"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
async def test_provider_errors_do_not_leak_secrets(snapshot, status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, text="sensitive-provider-body test-secret")
    )) as client:
        with pytest.raises(ProviderError) as error:
            await decide(client, "gemini", "test", "test-secret", "wait", Snapshot(**snapshot))
    assert "test-secret" not in str(error.value)
    assert "sensitive-provider-body" not in str(error.value)


@pytest.mark.parametrize("content", ["not json", "{}", '{"action":"teleport"}', json.dumps({**VALID, "action": "attack"})])
async def test_bad_model_action_rejected(snapshot, content):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )) as client:
        with pytest.raises(ProviderError):
            await decide(client, "openai", "test", "test-secret", "wait", Snapshot(**snapshot))


async def test_timeout_is_safe(snapshot):
    def handle(request):
        raise httpx.ReadTimeout("secret-url?key=test-secret", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError) as error:
            await decide(client, "openai", "test", "test-secret", "wait", Snapshot(**snapshot))
    assert "test-secret" not in str(error.value)
