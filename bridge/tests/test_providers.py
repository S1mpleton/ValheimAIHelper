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


@pytest.mark.parametrize("provider", ["gemini", "openai"])
async def test_404_has_safe_provider_guidance_without_retries(snapshot, provider):
    from valheim_ai.providers import ProviderModelNotFound
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND", "message": "sensitive-provider-body test-secret"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderModelNotFound) as error:
            await decide(client, provider, "missing-model", "test-secret", "wait", Snapshot(**snapshot))
    message = str(error.value)
    assert ("Gemini" if provider == "gemini" else "OpenAI") in message
    assert "404" in message and "примените настройки" in message
    assert "test-secret" not in message and "sensitive-provider-body" not in message
    assert len(calls) == 1


@pytest.mark.parametrize("body", ['<html>gateway test-secret</html>', '{"unexpected":"test-secret"}'])
async def test_nonstandard_404_does_not_claim_model_is_missing(snapshot, body):
    from valheim_ai.providers import ProviderModelNotFound
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(404, text=body)
    )) as client:
        with pytest.raises(ProviderModelNotFound) as error:
            await decide(client, "gemini", "test", "test-secret", "wait", Snapshot(**snapshot))
    assert "без стандартного ответа" in str(error.value)
    assert "прокси/VPN" in str(error.value)
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize("statuses,delays", [([503, 200], [2]), ([503, 503, 200], [2, 4]), ([503, 503, 503], [2, 4]), ([429], [])])
async def test_retry_is_bounded_and_pauses(snapshot, monkeypatch, statuses, delays):
    from valheim_ai import providers
    pauses, calls = [], []
    async def sleep(seconds):
        pauses.append(seconds)
    monkeypatch.setattr(providers.asyncio, "sleep", sleep)
    def handle(request):
        code = statuses[len(calls)]
        calls.append(request)
        return httpx.Response(code, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if statuses[-1] == 200:
            assert (await decide(client, "openai", "test", "secret", "wait", Snapshot(**snapshot))).action == "follow"
        else:
            with pytest.raises(ProviderError):
                await decide(client, "openai", "test", "secret", "wait", Snapshot(**snapshot))
    assert pauses == delays
    assert len(calls) == len(statuses)


async def test_gemini_models_pagination_and_filtering():
    from valheim_ai.providers import list_gemini_models
    calls = []
    def handle(request):
        assert request.headers["x-goog-api-key"] == "secret"
        assert "secret" not in str(request.url)
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/invalid/name", "supportedGenerationMethods": ["generateContent"]},
            ], "nextPageToken": "second"})
        assert request.url.params["pageToken"] == "second"
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-flash", "supportedGenerationMethods": ["generateContent"]},
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await list_gemini_models(client, "secret") == ["gemini-flash", "gemini-pro"]


async def test_gemini_models_rejects_pagination_loop():
    from valheim_ai.providers import list_gemini_models
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"nextPageToken": "same"}
    ))) as client:
        with pytest.raises(ProviderError):
            await list_gemini_models(client, "secret")


async def test_api_attempt_deadline(monkeypatch):
    import asyncio
    from valheim_ai import providers
    monkeypatch.setattr(providers, "API_TIMEOUT", 0.01)
    async def handle(request):
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError, match="не ответил"):
            await providers.list_gemini_models(client, "secret")


def test_timeout_budgets_match_mod():
    from pathlib import Path
    from valheim_ai.providers import API_TIMEOUT, DECISION_TIMEOUT, CONNECTED_TIMEOUT, RETRY_DELAYS
    assert API_TIMEOUT * (len(RETRY_DELAYS) + 1) + sum(RETRY_DELAYS) < DECISION_TIMEOUT
    plugin = (Path(__file__).parents[2] / "mod/ValheimAIHelper/Plugin.cs").read_text(encoding="utf-8")
    import re
    mod_timeout = int(re.search(r"request.timeout = (\d+)", plugin)[1])
    assert DECISION_TIMEOUT < mod_timeout
    assert CONNECTED_TIMEOUT > mod_timeout + 60


@pytest.mark.parametrize("failed_step", [0, 1, 2])
async def test_gemini_diagnostic_identifies_failed_stage_and_redacts_key(snapshot, failed_step):
    from valheim_ai.providers import diagnose_gemini
    calls = []
    key = "private-google-key"
    def handle(request):
        step = len(calls)
        calls.append(request)
        if step == failed_step:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "message":
                "models/test is not found for API version v1beta or is not supported for generateContent. key=" + key}})
        if request.method == "GET":
            return httpx.Response(200, json={"supportedGenerationMethods": ["generateContent"]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(VALID)}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError) as error:
            await diagnose_gemini(client, "test", key, Snapshot(**snapshot))
    message = str(error.value)
    assert len(calls) == failed_step + 1
    assert "v1beta" in message and "Ответ Google:" in message
    assert "not supported for generateContent" in message
    assert key not in message
    assert ["Карточка модели (GET", "Простая генерация (POST", "Генерация действия с JSON-схемой"][failed_step] in message
    if failed_step > 0:
        assert "Карточка модели: OK" in message
    if failed_step > 1:
        assert "Простая генерация: OK" in message


def test_diagnostic_redacts_before_truncation_and_does_not_show_headers_or_bodies():
    from valheim_ai.providers import safe_google_detail
    key = "private-key-crossing-output-boundary"
    response = httpx.Response(404, json={"error": {"message": "x" * 595 + key + "tail"}})
    detail = safe_google_detail(response, key)
    assert key not in detail and "private" not in detail
    assert len(detail) <= 600
    assert safe_google_detail(httpx.Response(404, text="<html>private-key</html>"), "private-key") == ""


@pytest.mark.parametrize('provider,model', [('openai', 'gpt-6-luna'), ('gemini', 'gemini-3.8-flash')])
async def test_current_models_use_low_reasoning_and_structured_actions(snapshot, provider, model):
    def handle(request):
        body = json.loads(request.content)
        if provider == 'openai':
            assert body['reasoning_effort'] == 'low'
            assert body['max_completion_tokens'] >= 1024
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(VALID)}}]})
        assert body['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'low'}
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(VALID)}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert (await decide(client, provider, model, 'unit-key', 'follow', Snapshot(**snapshot))).action == 'follow'


async def test_openai_list_filters_non_chat_models_and_uses_header_auth():
    from valheim_ai.providers import list_openai_models
    def handle(request):
        assert request.headers['authorization'] == 'Bearer unit-key'
        assert 'unit-key' not in str(request.url)
        return httpx.Response(200, json={'data': [{'id': name} for name in [
            'gpt-6-luna', 'gpt-4.1-mini', 'gpt-image-1', 'gpt-audio', 'gpt-realtime', 'embedding-model', 'invalid/path'
        ]]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await list_openai_models(client, 'unit-key') == ['gpt-4.1-mini', 'gpt-6-luna']


async def test_openai_diagnostic_redacts_bearer_key(snapshot):
    key = 'unit-private-openai-key'
    def handle(request):
        return httpx.Response(400, json={'error': {'message': 'invalid request with key=' + key}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError) as error:
            await decide(client, 'openai', 'gpt-6-luna', key, 'wait', Snapshot(**snapshot))
    assert key not in error.value.diagnostic
    assert '[REDACTED]' in error.value.diagnostic
