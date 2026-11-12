import asyncio
import json

import httpx
import pytest

from test_ollama import app_client, cycle, plan, reply, start, MODEL
from valheim_ai.local_schema import action_schema
from valheim_ai.ollama import ModelSession
from valheim_ai.performance import ollama_statistics
from valheim_ai.protocol import Decision, Snapshot, validate_decision
from valheim_ai.app import create_app


async def test_switch_unloads_all_other_running_models_before_new_generation_and_unchanged_model_stays_warm():
    running = {"old-a:latest", "old-b:latest"}
    calls = []
    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/api/ps": return httpx.Response(200, json={"models": [{"name": n} for n in sorted(running)]})
        body = json.loads(req.content)
        assert req.headers.get("authorization") is None
        if req.url.path == "/api/generate":
            assert body == {"model": body["model"], "keep_alive": 0, "stream": False, "think": False}
            running.remove(body["model"])
            return httpx.Response(200, json={"done": True})
        if req.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        assert not running or running == {"new:latest"}
        running.add("new:latest")
        return reply(plan())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ModelSession(lambda *args: None)
        await session.select(client, "http://localhost:11434", "new:latest")
        assert not running
        await session.generate(client, "http://127.0.0.1:11434", "new:latest", [], {}, {})
        await session.select(client, "http://localhost:11434", "new:latest")
        assert calls == ["/api/ps", "/api/generate", "/api/generate", "/api/ps", "/api/show", "/api/chat"]


async def test_failed_unload_never_starts_new_model():
    calls = []
    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"models": [{"name": "old:latest"}]}) if req.url.path == "/api/ps" else httpx.Response(503, json={"error": "busy"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ModelSession(lambda *args: None)
        with pytest.raises(Exception, match="503"): await session.select(client, "http://localhost:11434", "new:latest")
        assert session.selected is None and calls == ["/api/ps", "/api/generate"]


async def test_model_session_cancels_inference_before_switch_and_cannot_reload_old_model():
    entered, aborted = asyncio.Event(), asyncio.Event()
    running = {"old:latest"}
    async def handler(req):
        if req.url.path == "/api/ps": return httpx.Response(200, json={"models": [{"name": n} for n in running]})
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        if req.url.path == "/api/generate": running.clear(); return httpx.Response(200, json={"done": True})
        entered.set()
        try: await asyncio.Event().wait()
        finally: aborted.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = ModelSession(lambda *args: None)
        old = asyncio.create_task(session.generate(client, "http://localhost:11434", "old:latest", [], {}, {}))
        await asyncio.wait_for(entered.wait(), 1)
        old.cancel(); await asyncio.gather(old, return_exceptions=True)
        await session.select(client, "http://localhost:11434", "new:latest")
        assert aborted.is_set() and not running and session.selected[1] == "new:latest"


async def test_configure_cancels_live_plan_before_selecting_new_model(snapshot):
    entered, aborted = asyncio.Event(), asyncio.Event()
    async def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        entered.set()
        try: await asyncio.Event().wait()
        finally: aborted.set()
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        snapshot["sequence"] += 1; await c.post("/api/decision", json=snapshot)
        await asyncio.wait_for(entered.wait(), 1)
        configured = await asyncio.wait_for(c.post("/api/provider", json={"provider": "ollama", "model": "new:local"}), 1)
        assert configured.status_code == 200 and aborted.is_set()
        assert not app.state.bridge.inference_tasks and app.state.bridge.planner.task is None
        assert app.state.bridge.model == "new:local" and app.state.bridge.command.mode == "stop"


async def test_checking_different_model_does_not_automatically_reload_previous_ai_model(snapshot):
    models = []
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        models.append(json.loads(req.content)["model"])
        return reply(plan())
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        result = await c.post("/api/check/ollama", json={"model": "new:local"})
        assert result.status_code == 200 and app.state.bridge.command.mode == "stop"
        snapshot["sequence"] += 1; await c.post("/api/decision", json=snapshot)
        await asyncio.sleep(0)
        assert models == ["new:local"] and app.state.bridge.planner.task is None


async def test_stop_during_model_switch_cancels_it_without_adopting_new_settings():
    entered = asyncio.Event()
    async def handler(req):
        assert req.url.path == "/api/ps"
        entered.set(); await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as local:
        app = create_app(token="switch-test", client=local, ollama_client=local)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"Authorization": "Bearer switch-test"}) as c:
            pending = asyncio.create_task(c.post("/api/provider", json={"provider": "ollama", "model": "new:local"}))
            await asyncio.wait_for(entered.wait(), 1)
            assert (await c.get("/api/status")).json()["switching_model"]
            assert (await c.post("/api/command", json={"mode": "stop"})).status_code == 200
            assert (await asyncio.wait_for(pending, 1)).status_code == 409
            assert app.state.bridge.model == "" and app.state.bridge.command.mode == "stop"


def test_ground_drops_beyond_old_20m_limit_and_temporary_ownership_are_valid(snapshot):
    snapshot["supported_actions"] = ["pickup"]
    snapshot["entities"][-1].update(distance=100, position={"x": 100, "y": 0, "z": 0}, count=9, pickup_allowed=False)
    world = Snapshot.model_validate(snapshot)
    d = Decision(action="pickup", target_id="drop", duration=1, say="Подойду и подберу")
    assert validate_decision(d, world).action.value == "pickup"
    snapshot["entities"][-1]["position"]["x"] = 129
    with pytest.raises(ValueError): validate_decision(d, Snapshot.model_validate(snapshot))


def test_give_requires_player_real_inventory_and_exact_available_count(snapshot):
    snapshot["supported_actions"] = ["give"]
    world = Snapshot.model_validate(snapshot)
    d = Decision(action="give", target_id="leader", item_id="inventory:0", amount=2, duration=1, say="Дам ягоды")
    assert validate_decision(d, world) == d
    for changes in ({"target_id": "drop"}, {"item_id": "drop"}, {"amount": 3}, {"target_id": "invented"}):
        with pytest.raises(ValueError): validate_decision(d.model_copy(update=changes), world)


def test_local_schema_forbids_giving_to_ground_drop_and_requires_inventory_id(snapshot):
    snapshot["supported_actions"] = ["give", "pickup", "drop"]
    variants = action_schema(Snapshot.model_validate(snapshot), True)["$defs"]["Decision"]["anyOf"]
    give = next(v for v in variants if v["properties"]["action"]["const"] == "give")
    assert give["properties"]["target_id"]["enum"] == ["leader"]
    assert give["properties"]["item_id"]["enum"] == ["inventory:0", "inventory:1"]
    assert "item_id" in give["required"] and "amount" in give["required"]
    assert "destination" not in give["properties"]


async def test_all_reported_ground_drops_reach_local_model_without_second_truncation(snapshot):
    snapshot["supported_actions"] = ["pickup"]
    snapshot["entities"] += [dict(snapshot["entities"][-1], id=f"drop:{i}", count=i + 1) for i in range(60)]
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        content = json.loads(req.content)["messages"][1]["content"]
        data = json.loads(content.split("\n", 1)[1])
        assert len([e for e in data["entities"] if e["kind"] == "item"]) == 61
        assert any(e["id"] == "drop:59" and e["count"] == 60 for e in data["entities"])
        return reply(plan("pickup", "drop:59"))
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        assert (await cycle(app, c, snapshot)).json()["action"] == "pickup"


async def test_explicit_chat_ack_is_immediate_and_task_is_not_ignored_when_model_omits_intent(snapshot):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        entered.set(); await release.wait()
        assert "Подбери камень" in json.loads(req.content)["messages"][-1]["content"]
        return reply(plan("pickup", "drop"))
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        result = await c.post("/api/chat", json={"actor_name": "Эйрик", "speaker": "Лидер", "text": "Эйрик, Подбери камень", "explicit_address": True})
        assert result.json()["queued"] and app.state.bridge.command.objective == "Эйрик, Подбери камень"
        assert any(e["source"] == "assistant" and "Принял обращение" in e["message"] for e in app.state.bridge.events)
        snapshot["sequence"] += 1; await c.post("/api/decision", json=snapshot)
        await asyncio.wait_for(entered.wait(), 1)
        status = (await c.get("/api/status")).json()
        assert status["performance"]["current"]["status"] == "running"
        task = app.state.bridge.planner.task; release.set(); await task
        snapshot["sequence"] += 1
        assert (await c.post("/api/decision", json=snapshot)).json()["action"] == "pickup"
        assert app.state.bridge.explicit_chat_mission is None


async def test_inventory_failure_and_confirmed_transfer_are_reported_once_and_not_repeated(snapshot):
    snapshot["supported_actions"] = ["wait", "follow", "give"]
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        return reply(plan("give", "leader", item_id="inventory:0", amount=2))
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        lease = (await cycle(app, c, snapshot)).json()
        snapshot.update(sequence=snapshot["sequence"] + 1, plan_id=lease["plan_id"], action_status="blocked", previous_result="Нет пешего маршрута")
        await c.post("/api/decision", json=snapshot)
        snapshot["sequence"] += 1; await c.post("/api/decision", json=snapshot)
        failures = [e for e in app.state.bridge.events if e["source"] == "assistant" and "Нет пешего маршрута" in e["message"]]
        assert len(failures) == 1 and not app.state.bridge.planner.steps
        app.state.bridge.next_ai_request = app.state.bridge.retry_after = 0
        lease = (await cycle(app, c, snapshot)).json()
        snapshot.update(sequence=snapshot["sequence"] + 1, plan_id=lease["plan_id"], action_status="success", previous_result="Рядом с лидером оставлено: Малина ×2")
        result = (await c.post("/api/decision", json=snapshot)).json()
        assert result["action"] == "follow" and "Поручение выполнено" in app.state.bridge.command.objective
        assert sum("Малина ×2" in e["message"] for e in app.state.bridge.events if e["source"] == "assistant") == 1


async def test_invalid_plan_explains_field_without_echoing_model_output_and_stops_after_three(snapshot):
    snapshot["supported_actions"] = ["wait", "drop"]
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        return reply(plan("drop", "PRIVATE-INVENTED-INPUT", amount=999))
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        for _ in range(3):
            app.state.bridge.next_ai_request = app.state.bridge.retry_after = 0
            await cycle(app, c, snapshot)
        status = await c.get("/api/status")
        assert status.json()["mode"] == "stop" and "PRIVATE-INVENTED-INPUT" not in status.text
        assert "шаг 1 (drop)" in status.text and any(e["source"] == "assistant" and "Не могу выполнить" in e["message"] for e in status.json()["events"])


async def test_model_cannot_present_planned_pickup_as_already_completed(snapshot):
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        return reply(plan("pickup", "drop", say="Я уже подобрал всё и передал лидеру"))
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        lease = (await cycle(app, c, snapshot)).json()
        assert lease["say"].startswith("Подхожу к выпавшей стопке")
        assert not any("уже подобрал" in e["message"] for e in app.state.bridge.events)
        assert not any(e["source"] == "assistant" and "Предмет подобран" in e["message"] for e in app.state.bridge.events)


def test_local_plan_cannot_invent_chat_or_long_control_lease_without_pending_messages(snapshot):
    schema = action_schema(Snapshot.model_validate(snapshot), True)
    assert schema["properties"]["chat_intent"] == {"type": "null"}
    assert schema["properties"]["objective_complete"]["const"] is False
    for action in schema["$defs"]["Decision"]["anyOf"]:
        assert action["properties"]["duration"]["const"] == 1


def test_nanoseconds_statistics_and_missing_or_invalid_values():
    assert ollama_statistics({"eval_count": 100, "eval_duration": 2_000_000_000, "load_duration": 500_000_000}) == {
        "output_tokens": 100, "generation_seconds": 2.0, "tokens_per_second": 50.0, "load_seconds": 0.5}
    assert "tokens_per_second" not in ollama_statistics({"eval_count": 100, "eval_duration": 0})
    assert ollama_statistics({"eval_count": float("nan"), "load_duration": -1, "eval_duration": True}) == {}


async def test_speed_is_recorded_even_when_json_plan_is_rejected(snapshot):
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        return httpx.Response(200, json={"message": {"content": "{}"}, "load_duration": 1_000_000_000,
            "prompt_eval_duration": 2_000_000_000, "eval_duration": 4_000_000_000, "eval_count": 40, "prompt_eval_count": 300})
    async with app_client(handler) as (app, c):
        await start(c, snapshot); await cycle(app, c, snapshot)
        metrics = (await c.get("/api/status")).json()["performance"]
        assert metrics["current"] is None
        assert metrics["last"]["tokens_per_second"] == 10 and metrics["last"]["status"] == "error"
        assert metrics["last"]["load_seconds"] == 1 and metrics["last"]["prompt_tokens"] == 300
