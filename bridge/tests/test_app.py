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
    assert (await c.post("/api/command", headers=AUTH, content="x" * 65537)).status_code == 413


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
