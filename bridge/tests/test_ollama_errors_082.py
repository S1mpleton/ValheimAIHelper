"""Ollama must report the actual failure without guessing from model names."""
import json

import httpx
import pytest

from valheim_ai.ollama import DEFAULT_URL, generate, request, thinking_option
from valheim_ai.providers import (
    ProviderLocalConfigurationError, ProviderModelNotFound, ProviderUnavailable,
)


@pytest.mark.parametrize("code,detail,error_type", [
    (400, "failed to load model qwen-thinking:4b: invalid model data", ProviderLocalConfigurationError),
    (400, "cannot parse JSON schema for /models/think/model.gguf", ProviderLocalConfigurationError),
    (404, "model think-local:4b not found", ProviderModelNotFound),
    (503, "think-local:4b requires more system memory", ProviderUnavailable),
    (500, "failed to allocate CUDA memory for thinking-model", ProviderLocalConfigurationError),
])
async def test_incidental_think_does_not_claim_reasoning_is_unsupported(code, detail, error_type):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(code, json={"error": detail}))) as client:
        with pytest.raises(error_type) as caught:
            await request(client, DEFAULT_URL, "POST", "/api/chat", payload={"think": False})
    assert f"HTTP {code}" in str(caught.value)
    assert "отключение размышлений" not in str(caught.value)
    assert caught.value.diagnostic == detail


@pytest.mark.parametrize("detail", [
    "this model does not support thinking",
    "model doesn't support the think option",
    "unknown field \"think\"",
    "unrecognized parameter: think",
    "option 'think' is not supported",
    "thinking value 'false' is not allowed",
    "cannot disable thinking for this model",
])
@pytest.mark.parametrize("code", [400, 422])
async def test_explicit_false_rejection_preserves_actual_response_and_never_retries(detail, code):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(code, json={"error": detail})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderLocalConfigurationError) as caught:
            await request(client, DEFAULT_URL, "POST", "/api/chat", payload={"think": False})
    assert "отключение размышлений think=false" in str(caught.value)
    assert f"HTTP {code}" in str(caught.value)
    assert caught.value.diagnostic == detail
    assert len(seen) == 1 and json.loads(seen[0].content)["think"] is False


@pytest.mark.parametrize("path,payload", [
    ("/api/show", {"model": "think-model"}),
    ("/api/chat", {"model": "think-model"}),
    ("/api/chat", {"think": True}),
])
async def test_unsupported_parameter_only_describes_the_request_that_was_rejected(path, payload):
    detail = "unknown field think"
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"error": detail}))) as client:
        with pytest.raises(ProviderLocalConfigurationError) as caught:
            await request(client, DEFAULT_URL, "POST", path, payload=payload)
    assert "отключение размышлений" not in str(caught.value)
    assert caught.value.diagnostic == detail


async def test_error_diagnostic_is_bounded_and_credentials_are_redacted_before_truncation():
    secret = "sk-" + "a" * 40
    detail = f"invalid think model; key=private-value; {secret}; Bearer private-token; https://private.example/error " + "x" * 1000
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"error": detail}))) as client:
        with pytest.raises(ProviderLocalConfigurationError) as caught:
            await request(client, DEFAULT_URL, "POST", "/api/chat", payload={"think": False})
    diagnostic = caught.value.diagnostic
    assert len(diagnostic) <= 600
    assert all(secret not in diagnostic for secret in (secret, "private-value", "private-token", "private.example"))
    assert "invalid think model" in diagnostic


@pytest.mark.parametrize("metadata", [{}, {"thinking": {}}, {"thinking": {"values": []}},
                                       {"details": {"family": "qwen3"}}])
def test_unspecified_thinking_values_are_not_proof_of_mandatory_reasoning(metadata):
    assert thinking_option(metadata) is False


@pytest.mark.parametrize("metadata", [
    {"thinking": {"values": [True]}},
    {"thinking": {"values": ["low", "medium", "high"]}},
    {"details": {"family": "GPT-OSS"}},
])
async def test_known_reasoning_only_metadata_stops_before_generation(metadata):
    seen = []
    def handler(req):
        seen.append(req)
        assert req.url.path == "/api/show"
        return httpx.Response(200, json=metadata)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderLocalConfigurationError, match="размышлен"):
            await generate(client, DEFAULT_URL, "local-model", [], {})
    assert len(seen) == 1


async def test_generation_forwards_explicit_false_schema_options_and_token_budget_without_credentials():
    messages = [{"role": "user", "content": "follow the leader"}]
    schema = {"type": "object", "properties": {"tasks": {"type": "array"}}}
    requests = []
    def handler(req):
        requests.append(req)
        assert req.headers.get("authorization") is None
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"], "thinking": {"values": [False, True]}})
        assert req.url.path == "/api/chat"
        body = json.loads(req.content)
        assert body["think"] is False and body["stream"] is False
        assert body["options"] == {"temperature": 0, "num_ctx": 16384, "num_predict": 777}
        assert body["messages"] == messages and body["format"] == schema
        assert body["keep_alive"] == "10m"
        return httpx.Response(200, json={"done": True, "message": {"content": '{"tasks":[]}', "thinking": "untrusted"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await generate(client, DEFAULT_URL, "qwen3:4b", messages, schema, num_predict=777) == '{"tasks":[]}'
    assert len(requests) == 2
