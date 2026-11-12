import asyncio
import json

import httpx
import pytest

from valheim_ai.app import create_app, load_token


TOKEN = "test-pairing-code-long-enough-for-tests"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
async def clients():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "action": "pickup", "target_id": "drop", "duration": 1, "say": "Подбираю камень"
        })}}]})
    )) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
            yield app, client


async def test_pairing_required(clients):
    app, c = clients
    assert (await c.get("/health")).json()["status"] == "ok"
    assert (await c.get("/api/status")).status_code == 401
    assert (await c.get("/api/status", headers=AUTH)).status_code == 200


async def test_local_follow_without_keys_and_replay_rejected(clients, snapshot):
    app, c = clients
    first = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert first.json()["action"] == "wait"
    await c.post("/api/command", headers=AUTH, json={"mode": "follow"})
    snapshot["sequence"] += 1
    following = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert following.json()["action"] == "follow"
    assert following.json()["target_id"] == "leader"
    assert (await c.post("/api/decision", headers=AUTH, json=snapshot)).status_code == 409


async def test_new_character_starts_stopped_and_cannot_steal_live_session(clients, snapshot):
    _, c = clients
    await c.post("/api/decision", headers=AUTH, json=snapshot)
    await c.post("/api/command", headers=AUTH, json={"mode": "follow"})
    snapshot["session_id"] = "new-session"
    snapshot["actor"]["id"] = "other-player"
    assert (await c.post("/api/decision", headers=AUTH, json=snapshot)).status_code == 409


async def test_reenable_same_character_resets_to_stop(clients, snapshot):
    _, c = clients
    await c.post("/api/decision", headers=AUTH, json=snapshot)
    await c.post("/api/command", headers=AUTH, json={"mode": "follow"})
    snapshot["session_id"] = "new-session"
    assert (await c.post("/api/decision", headers=AUTH, json=snapshot)).json()["action"] == "wait"


async def test_ai_end_to_end_with_mock_provider(clients, snapshot):
    app, c = clients
    await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert (await c.post("/api/command", headers=AUTH, json={"mode": "ai"})).status_code == 409
    assert (await c.post("/api/provider", headers=AUTH, json={"provider": "openai", "model": "gpt-4.1-mini", "api_key": "do-not-return-this"})).status_code == 200
    await c.post("/api/command", headers=AUTH, json={"mode": "ai", "objective": "Подбери камень"})
    snapshot["sequence"] += 1
    result = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert result.status_code == 200
    assert result.json()["action"] == "pickup"
    assert result.json()["source"] == "openai"
    status = await c.get("/api/status", headers=AUTH)
    assert status.json()["keys_present"]["openai"] is True
    assert "do-not-return-this" not in status.text


async def test_stop_invalidates_inflight_model_decision(snapshot):
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_provider(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "action": "attack", "target_id": "enemy", "duration": 3, "say": "Атакую"
        })}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_provider)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            await c.post("/api/decision", headers=AUTH, json=snapshot)
            await c.post("/api/provider", headers=AUTH, json={"provider": "openai", "model": "test", "api_key": "secret"})
            await c.post("/api/command", headers=AUTH, json={"mode": "ai"})
            snapshot["sequence"] += 1
            task = asyncio.create_task(c.post("/api/decision", headers=AUTH, json=snapshot))
            await asyncio.wait_for(entered.wait(), 2)
            await c.post("/api/command", headers=AUTH, json={"mode": "stop"})
            release.set()
            result = await task
            assert result.json()["action"] == "wait"
            assert result.json()["source"] == "fallback"


async def test_host_origin_and_body_limits(clients):
    _, c = clients
    assert (await c.get("/", headers={"Host": "attacker.example"})).status_code == 400
    assert (await c.post("/api/command", headers={**AUTH, "Origin": "https://attacker.example"}, json={"mode": "stop"})).status_code == 403
    assert (await c.get("/", headers={"Sec-Fetch-Site": "cross-site"})).status_code == 403
    assert (await c.post("/api/command", headers=AUTH, content="x" * 131073)).status_code == 413


async def test_invalid_settings_never_echo_secret(clients):
    _, c = clients
    result = await c.post("/api/provider", headers=AUTH, json={"provider": "openai", "model": "test", "api_key": {"secret": "never-echo-me"}})
    assert result.status_code == 422
    assert "never-echo-me" not in result.text


async def test_dashboard_served_without_exposing_token(clients):
    _, c = clients
    page = await c.get("/")
    assert page.status_code == 200
    assert "Valheim" in page.text
    assert TOKEN not in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert (await c.get("/app.js")).status_code == 200


def test_pairing_token_reused(tmp_path):
    first = load_token(tmp_path)
    assert len(first) >= 32
    assert load_token(tmp_path) == first
    if __import__("os").name != "nt":
        assert (tmp_path / "bridge-token").stat().st_mode & 0o777 == 0o600


async def test_provider_failure_returns_wait_without_secret(snapshot):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, text="sensitive secret")
    )) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            await c.post("/api/decision", headers=AUTH, json=snapshot)
            await c.post("/api/provider", headers=AUTH, json={"provider": "openai", "model": "test", "api_key": "sensitive secret"})
            await c.post("/api/command", headers=AUTH, json={"mode": "ai"})
            snapshot["sequence"] += 1
            result = await c.post("/api/decision", headers=AUTH, json=snapshot)
            assert result.json()["action"] == "wait"
            assert result.json()["source"] == "fallback"
            assert "401" in result.json()["say"]
            assert "sensitive secret" not in result.text


async def test_422_diagnostics_visible_and_clear_on_recovery(clients, snapshot):
    _, c = clients
    snapshot["actor"]["max_health"] = 0
    result = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert result.status_code == 422
    assert "actor.max_health" in result.json()["detail"]
    status = (await c.get("/api/status", headers=AUTH)).json()
    assert status["actor"] is None
    assert "actor.max_health" in status["snapshot_error"]["message"]
    assert status["events"][0]["source"] == "bridge"
    assert (await c.post("/api/command", headers=AUTH, json={"mode": "follow"})).status_code == 409
    assert (await c.post("/api/command", headers=AUTH, json={"mode": "stop"})).status_code == 200
    snapshot["actor"]["max_health"] = 25
    snapshot["actor"]["stamina"] = -.01
    assert (await c.post("/api/decision", headers=AUTH, json=snapshot)).status_code == 200
    status = (await c.get("/api/status", headers=AUTH)).json()
    assert status["snapshot_error"] is None
    assert status["actor"]["stamina"] == 0
    assert (await c.post("/api/command", headers=AUTH, json={"mode": "follow"})).status_code == 200


async def test_follow_and_ai_not_accepted_before_first_observation(clients):
    _, c = clients
    for mode in ["follow", "ai"]:
        result = await c.post("/api/command", headers=AUTH, json={"mode": mode})
        assert result.status_code == 409
        assert "F8" in result.json()["detail"]


async def test_validation_diagnostics_cannot_echo_unknown_keys_or_values(clients, snapshot):
    _, c = clients
    snapshot["unexpected-secret-as-key"] = "secret-as-value"
    result = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert result.status_code == 422
    assert "unexpected-secret-as-key" not in result.text
    assert "secret-as-value" not in result.text
    status = (await c.get("/api/status", headers=AUTH)).text
    assert "unexpected-secret-as-key" not in status
    assert "secret-as-value" not in status


async def test_unauthenticated_invalid_body_cannot_poison_dashboard(clients):
    _, c = clients
    await c.post("/api/decision", json={"bad": "request"})
    status = (await c.get("/api/status", headers=AUTH)).json()
    assert status["snapshot_error"] is None


async def test_follow_without_leader_explains_why_it_waits(clients, snapshot):
    _, c = clients
    snapshot["leader_id"] = ""
    await c.post("/api/decision", headers=AUTH, json=snapshot)
    await c.post("/api/command", headers=AUTH, json={"mode": "follow"})
    snapshot["sequence"] += 1
    result = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert result.json()["action"] == "wait"
    assert "Лидер не виден" in result.json()["say"]



async def test_models_auth_key_and_no_side_effects():
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["x-goog-api-key"] == "temporary-secret"
        return httpx.Response(200, json={"models": [{"name": "models/gemini-test", "supportedGenerationMethods": ["generateContent"]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            assert (await c.post("/api/models/gemini", json={})).status_code == 401
            assert (await c.post("/api/models/gemini", headers=AUTH, json={})).status_code == 409
            result = await c.post("/api/models/gemini", headers=AUTH, json={"api_key": "temporary-secret"})
            assert result.json() == {"models": ["gemini-test"]}
            assert len(calls) == 1
            assert app.state.bridge.keys["gemini"] == ""
            assert app.state.bridge.command.mode == "stop"
            app.state.bridge.keys["gemini"] = "temporary-secret"
            assert (await c.post("/api/models/gemini", headers=AUTH, json={})).status_code == 200


async def test_503_cooldown_prevents_new_requests(snapshot, monkeypatch):
    from valheim_ai import providers
    calls = []
    async def sleep(seconds):
        pass
    monkeypatch.setattr(providers.asyncio, "sleep", sleep)
    def handle(request):
        calls.append(request)
        return httpx.Response(503, text="secret response")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": "gemini", "model": "test", "api_key": "secret"})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1
            result = await c.post("/api/decision", json=snapshot)
            assert result.json()["action"] == "wait" and "503" in result.json()["say"]
            assert "secret" not in result.text
            assert len(calls) == 3
            snapshot["sequence"] += 1
            assert (await c.post("/api/decision", json=snapshot)).json()["action"] == "wait"
            assert len(calls) == 3
            app.state.bridge.retry_after = 0
            app.state.bridge.next_ai_request = 0
            snapshot["sequence"] += 1
            await c.post("/api/decision", json=snapshot)
            assert len(calls) == 6


async def test_total_deadline_returns_wait_and_releases_busy(snapshot, monkeypatch):
    from valheim_ai import app as app_module
    monkeypatch.setattr(app_module, "DECISION_TIMEOUT", 0.01)
    async def handle(request):
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": "gemini", "model": "test", "api_key": "secret"})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1
            result = await c.post("/api/decision", json=snapshot)
            assert result.json()["action"] == "wait"
            assert result.json()["source"] == "fallback"
            assert not app.state.bridge.busy


async def test_404_stops_ai_and_recovers_after_model_change(snapshot):
    calls = []
    def handle(request):
        calls.append(request)
        if "missing-model" in request.url.path:
            return httpx.Response(404, text="sensitive-body secret-key")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "action": "follow", "target_id": "leader", "duration": 2, "say": "Следую"
        })}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": "gemini", "model": "missing-model", "api_key": "secret-key"})
            revision = (await c.post("/api/command", json={"mode": "ai"})).json()["revision"]
            snapshot["sequence"] += 1
            result = await c.post("/api/decision", json=snapshot)
            assert result.json()["action"] == "wait"
            assert result.json()["command_revision"] > revision
            assert "AI остановлен" in result.json()["say"] and "404" in result.json()["say"]
            assert "secret-key" not in result.text and "sensitive-body" not in result.text
            assert (await c.get("/api/status")).json()["mode"] == "stop"
            snapshot["sequence"] += 1
            assert (await c.post("/api/decision", json=snapshot)).json()["action"] == "wait"
            assert len(calls) == 1
            await c.post("/api/provider", json={"provider": "gemini", "model": "available-model"})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1
            assert (await c.post("/api/decision", json=snapshot)).json()["action"] == "follow"
            assert len(calls) == 2


async def test_old_404_does_not_stop_new_ai_configuration(snapshot):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handle(request):
        entered.set()
        await release.wait()
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": "gemini", "model": "old", "api_key": "secret"})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1
            task = asyncio.create_task(c.post("/api/decision", json=snapshot))
            await asyncio.wait_for(entered.wait(), 2)
            await c.post("/api/provider", json={"provider": "gemini", "model": "new"})
            await c.post("/api/command", json={"mode": "ai"})
            release.set()
            result = await task
            assert "старый ответ отменён" in result.json()["say"]
            status = (await c.get("/api/status")).json()
            assert status["mode"] == "ai" and status["model"] == "new"


async def test_gemini_check_uses_typed_model_and_does_not_change_control():
    # Create a separate upstream: the check has no live game observation or actor.
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["x-goog-api-key"] == "temporary-key"
        if request.method == "GET":
            assert request.url.path.endswith("/models/probe-model")
            return httpx.Response(200, json={"supportedGenerationMethods": ["generateContent"]})
        assert request.url.path.endswith("/models/probe-model:generateContent")
        if "responseJsonSchema" in json.loads(request.content)["generationConfig"]:
            assert b'api-check' in request.content
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "action": "wait", "target_id": "", "duration": 1, "say": "OK"
        })}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            assert (await c.post("/api/check/gemini", json={"model": "probe-model"})).status_code == 401
            assert (await c.post("/api/check/gemini", headers=AUTH, json={"model": "probe-model"})).status_code == 409
            result = await c.post("/api/check/gemini", headers=AUTH, json={"model": "probe-model", "api_key": "temporary-key"})
            assert result.json()["ok"] is True and result.json()["model"] == "probe-model"
            assert result.json()["checks"] == ["Карточка модели: OK", "Простая генерация: OK", "Формат действий: OK"]
            assert len(calls) == 3
            assert app.state.bridge.keys["gemini"] == ""
            assert app.state.bridge.provider == "offline"
            assert app.state.bridge.command.mode == "stop"
            assert app.state.bridge.last_snapshot is None


@pytest.mark.parametrize("standard", [True, False])
async def test_gemini_check_404_reports_response_format_without_secrets(standard):
    def handle(request):
        if standard:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "message": "private-key"}})
        return httpx.Response(404, text="private-key proxy gateway")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        app.state.bridge.keys["gemini"] = "private-key"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            result = await c.post("/api/check/gemini", json={"model": "selected-model"})
            assert result.status_code == 502
            assert ("404 / NOT_FOUND" if standard else "без стандартного ответа") in result.json()["detail"]
            assert "private-key" not in result.text
            assert app.state.bridge.command.mode == "stop"


async def test_quota_429_stops_automatic_ai_requests(snapshot):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota exhausted"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": "gemini", "model": "test", "api_key": "test-key"})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1
            result = await c.post("/api/decision", json=snapshot)
            assert "AI остановлен" in result.json()["say"] and "429" in result.json()["say"]
            for _ in range(3):
                snapshot["sequence"] += 1
                assert (await c.post("/api/decision", json=snapshot)).json()["action"] == "wait"
            assert len(calls) == 1
            assert app.state.bridge.command.mode == "stop"


async def test_ai_interval_limits_calls_and_does_not_delay_local_follow(clients, snapshot):
    app, c = clients
    await c.post("/api/decision", headers=AUTH, json=snapshot)
    await c.post("/api/provider", headers=AUTH, json={"provider": "openai", "model": "test", "api_key": "key", "request_interval_seconds": 30})
    await c.post("/api/command", headers=AUTH, json={"mode": "ai"})
    snapshot["sequence"] += 1
    assert (await c.post("/api/decision", headers=AUTH, json=snapshot)).json()["action"] == "pickup"
    snapshot["sequence"] += 1
    paused = await c.post("/api/decision", headers=AUTH, json=snapshot)
    assert paused.json()["action"] == "wait" and "Пауза" in paused.json()["say"]
    await c.post("/api/command", headers=AUTH, json={"mode": "follow"})
    snapshot["sequence"] += 1
    assert (await c.post("/api/decision", headers=AUTH, json=snapshot)).json()["action"] == "follow"
    status = (await c.get("/api/status", headers=AUTH)).json()
    assert status["request_interval_seconds"] == 30


async def test_openai_in_game_setup_check_and_unknown_provider_are_safe():
    calls = []
    def handle(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'gpt-6-luna'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'action': 'wait', 'target_id': '', 'duration': 1, 'say': 'Связь работает'
        })}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost', headers=AUTH) as c:
            assert (await c.post('/api/models/openai', json={})).status_code == 409
            models = await c.post('/api/models/openai', json={'api_key': 'unit-key'})
            assert models.json() == {'models': ['gpt-6-luna']}
            checked = await c.post('/api/check/openai', json={'api_key': 'unit-key', 'model': 'gpt-6-luna'})
            assert checked.json()['ok'] is True
            assert len(calls) == 2
            assert app.state.bridge.keys['openai'] == ''
            assert app.state.bridge.command.mode == 'stop'
            assert (await c.post('/api/models/other', json={})).status_code == 404
            assert (await c.post('/api/check/other', json={'model': 'test'})).status_code == 404
            assert len(calls) == 2


async def test_actual_provider_error_is_visible_to_in_game_status_without_key(snapshot):
    key = 'unit-secret-key'
    def handle(request):
        return httpx.Response(404, json={'error': {'status': 'NOT_FOUND', 'message': 'Please use current model, key=' + key}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost', headers=AUTH) as c:
            await c.post('/api/decision', json=snapshot)
            await c.post('/api/provider', json={'provider': 'gemini', 'model': 'old-model', 'api_key': key})
            await c.post('/api/command', json={'mode': 'ai'})
            snapshot['sequence'] += 1
            await c.post('/api/decision', json=snapshot)
            result = await c.get('/api/status')
            assert 'Please use current model' in result.json()['provider_error']['detail']
            assert key not in result.text
            await c.post('/api/provider', json={'provider': 'gemini', 'model': 'new-model'})
            assert (await c.get('/api/status')).json()['provider_error'] is None
