import asyncio
import json

import httpx
import pytest

from valheim_ai.app import create_app
from valheim_ai.protocol import Decision, Snapshot, validate_decision
from valheim_ai.providers import strict_schema

AUTH = {"Authorization": "Bearer planning-test-token-long-enough"}


async def configure(c, snapshot):
    snapshot["background_planning"] = True
    snapshot["supported_actions"] = ["wait", "follow", "move", "jump", "attack", "defend", "flee", "dodge", "pickup", "interact", "eat", "equip", "use"]
    assert (await c.post("/api/decision", json=snapshot)).status_code == 200
    await c.post("/api/provider", json={"provider": "openai", "model": "gpt-6-luna", "api_key": "mock-secret", "request_interval_seconds": 20})
    await c.post("/api/command", json={"mode": "ai", "objective": "Следуй за лидером и помогай ему выжить."})


async def poll(c, snapshot, **feedback):
    snapshot["sequence"] += 1
    snapshot.update(feedback)
    r = await c.post("/api/decision", json=snapshot)
    assert r.status_code == 200, r.text
    return r.json()


async def test_slow_plan_does_not_block_follow_and_steps_advance_from_actual_feedback(snapshot):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def provider(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": [
            {"action": "equip", "target_id": "inventory:1", "duration": 1, "say": "Беру дубину"},
            {"action": "attack", "target_id": "enemy", "duration": 3, "say": "Защищаю лидера"},
            {"action": "follow", "target_id": "leader", "duration": 3, "say": "Возвращаюсь"},
        ]})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            try:
                await configure(c, snapshot)
                first = await poll(c, snapshot)
                assert first["action"] == "follow"
                await asyncio.wait_for(entered.wait(), 1)
                assert (await asyncio.wait_for(poll(c, snapshot), .5))["action"] == "follow"
                assert len(calls) == 1
                task = app.state.bridge.planner.task
                release.set()
                await task
                equip = await poll(c, snapshot)
                assert equip["action"] == "equip" and equip["plan_id"]
                renewed = await poll(c, snapshot, plan_id=equip["plan_id"], action_status="running")
                assert renewed["plan_id"] == equip["plan_id"]
                attack = await poll(c, snapshot, plan_id=equip["plan_id"], action_status="success", previous_result="Дубина экипирована")
                assert attack["action"] == "attack" and attack["plan_id"] != equip["plan_id"]
                assert "экипирована" in app.state.bridge.planner.history[-1]
                following = await poll(c, snapshot, plan_id=attack["plan_id"], action_status="success", previous_result="Враг побеждён")
                assert following["action"] == "follow"
                assert len(calls) == 1
            finally:
                await app.state.bridge.planner.close()


async def test_stop_cancels_background_plan_and_no_stale_controls_survive(snapshot):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def provider(request):
        entered.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await configure(c, snapshot)
            await poll(c, snapshot)
            await asyncio.wait_for(entered.wait(), 1)
            await c.post("/api/command", json={"mode": "stop"})
            await asyncio.wait_for(cancelled.wait(), 1)
            stopped = await poll(c, snapshot)
            assert stopped["action"] == "wait" and stopped["source"] == "local" and not stopped["plan_id"] and stopped["mode"] == "stop"
            assert app.state.bridge.planner.task is None


async def test_plan_revalidated_on_new_snapshot_and_blocked_prerequisite_cancels_tail(snapshot):
    async def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": [
            {"action": "equip", "target_id": "inventory:1", "duration": 1, "say": "Оружие"},
            {"action": "attack", "target_id": "enemy", "duration": 3, "say": "Бой"},
        ]})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            try:
                await configure(c, snapshot)
                await poll(c, snapshot)
                if app.state.bridge.planner.task is not None:
                    await app.state.bridge.planner.task
                equip = await poll(c, snapshot)
                result = await poll(c, snapshot, plan_id=equip["plan_id"], action_status="blocked", previous_result="Предмет отсутствует")
                assert result["action"] == "follow" and not app.state.bridge.planner.steps
                # A completed API call does not trigger another paid call on every snapshot.
                assert app.state.bridge.planner.task is None
            finally: await app.state.bridge.planner.close()


async def test_background_429_stops_and_does_not_retry_on_each_observation(snapshot):
    calls = []
    async def provider(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "quota for key=mock-secret"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await configure(c, snapshot)
            await poll(c, snapshot)
            if app.state.bridge.planner.task is not None:
                await app.state.bridge.planner.task
            for _ in range(3): assert (await poll(c, snapshot))["action"] == "wait"
            status = await c.get("/api/status")
            assert status.json()["mode"] == "stop" and "429" in status.text
            assert "mock-secret" not in status.text and len(calls) == 1


@pytest.mark.parametrize("destination", [{"x": 50, "y": 0, "z": 0}, {"x": 0, "y": 9, "z": 0}])
def test_model_cannot_request_unbounded_navigation(snapshot, destination):
    snapshot["supported_actions"] = ["move"]
    with pytest.raises(ValueError):
        validate_decision(Decision(action="move", target_id="", duration=3, say="", destination=destination), Snapshot.model_validate(snapshot))


def test_strict_provider_schema_requires_nullable_fields_without_defaults():
    schema = strict_schema(Decision)
    assert set(schema["required"]) == set(schema["properties"])
    assert "default" not in schema["properties"]["destination"]


async def test_target_disappearing_cancels_cached_attack_before_issuing_controls(snapshot):
    async def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": [
            {"action": "attack", "target_id": "enemy", "duration": 3, "say": "Защищаю"}
        ]})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await configure(c, snapshot)
            await poll(c, snapshot)
            task = app.state.bridge.planner.task
            if task is not None: await task
            assert (await poll(c, snapshot))["action"] == "attack"
            snapshot["entities"] = [e for e in snapshot["entities"] if e["id"] != "enemy"]
            assert (await poll(c, snapshot))["action"] == "follow"
            assert not app.state.bridge.planner.steps
            await app.state.bridge.planner.close()


async def test_background_503_uses_backoff_then_cooldown_while_following(snapshot, monkeypatch):
    from valheim_ai import providers
    monkeypatch.setattr(providers, "RETRY_DELAYS", (0, 0))
    calls = []
    async def provider(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "busy"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as c:
            await configure(c, snapshot)
            assert (await poll(c, snapshot))["action"] == "follow"
            task = app.state.bridge.planner.task
            if task is not None: await task
            assert len(calls) == 3
            for _ in range(3): assert (await poll(c, snapshot))["action"] == "follow"
            assert len(calls) == 3 and app.state.bridge.retry_after > 0
            await app.state.bridge.planner.close()
