"""A green connection check must exercise the NPC's production task contract."""
import asyncio
import copy
import json

import httpx
import pytest

from test_ollama import MODEL, app_client, check_batch, plan, reply
from valheim_ai.protocol import Snapshot
from valheim_ai.task_pool import TaskPool


@pytest.mark.parametrize("proposal", [
    plan(),
    {"tasks": []},
    {"tasks": [{"kind": "collect", "priority": 72}]},
    {"tasks": [{"kind": "follow", "priority": 31}]},
    {"tasks": [{"kind": "collect", "priority": 72, "item_prefabs": ["Wood"], "continuous": True},
               {"kind": "follow", "priority": 31}]},
    {"tasks": [{"kind": "collect", "priority": 72, "item_prefabs": ["Stone"], "continuous": False},
               {"kind": "follow", "priority": 31}]},
])
async def test_old_wait_contract_and_wrong_task_meaning_cannot_report_green(proposal):
    generated = []
    def handler(request):
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        generated.append(json.loads(request.content))
        return reply(proposal)
    async with app_client(handler) as (app, client):
        response = await client.post("/api/check/ollama", json={"model": MODEL})
        assert response.status_code == 502, response.text
        assert response.json()["detail"]
        assert 1 <= len(generated) <= 2, "Only one bounded schema repair is permitted"
        assert all(body["think"] is False for body in generated)
        assert not app.state.bridge.task_pool.tasks
        assert app.state.bridge.command.mode == "stop"
        assert app.state.bridge.check_task is None


async def test_check_uses_isolated_pool_and_keeps_models_priorities(monkeypatch, snapshot):
    upstream = []
    applied = []
    original_apply = TaskPool.apply_batch
    def capture_apply(self, batch, *args, **kwargs):
        result = original_apply(self, batch, *args, **kwargs)
        applied.append((self, [(n.kind, n.priority) for n in self.tasks]))
        return result
    monkeypatch.setattr(TaskPool, "apply_batch", capture_apply)
    def handler(request):
        upstream.append(request)
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        assert "private-world-character" not in request.content.decode()
        assert "private-user-order" not in request.content.decode()
        return reply(check_batch(follow_priority=7, collect_priority=89))
    async with app_client(handler) as (app, client):
        state = app.state.bridge
        snapshot.update(task_pool_supported=True, world_id="private-world")
        snapshot["actor"]["name"] = "private-world-character"
        state.last_snapshot = Snapshot.model_validate(snapshot)
        state.task_pool.apply_batch([{"id":"real-follow", "kind":"follow", "priority":17}],
                                    snapshot=state.last_snapshot, source_request="private-user-order")
        before = copy.deepcopy(state.task_pool.status())
        revision, command = state.revision, state.command.model_dump()
        response = await client.post("/api/check/ollama", json={"model": MODEL})
        assert response.status_code == 200, response.text
        assert response.json()["checks"][-2:] == ["Формат пула задач: OK", "Следование и сбор: OK"]
        assert state.task_pool.status() == before
        assert state.revision == revision and state.command.model_dump() == command
        assert state.last_snapshot.actor.name == "private-world-character"
        isolated = [rows for pool, rows in applied if pool is not state.task_pool]
        assert isolated and any(sorted(rows)==[("collect",89),("follow",7)] for rows in isolated)
        assert [request.url.path for request in upstream] == ["/api/show", "/api/chat"]


async def test_stop_cancels_schema_repair_without_leaving_a_background_request():
    entered, aborted = asyncio.Event(), asyncio.Event()
    generations = 0
    async def handler(request):
        nonlocal generations
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities":["completion"]})
        generations += 1
        if generations == 1: return reply({"tasks":[{"kind":"collect", "priority":72}]})
        entered.set()
        try: await asyncio.Event().wait()
        finally: aborted.set()
    async with app_client(handler) as (app, client):
        pending = asyncio.create_task(client.post("/api/check/ollama", json={"model":MODEL}))
        await asyncio.wait_for(entered.wait(), 2)
        assert (await client.post("/api/command", json={"mode":"stop"})).status_code == 200
        assert (await asyncio.wait_for(pending, 2)).status_code == 409
        await asyncio.wait_for(aborted.wait(), 2)
        assert app.state.bridge.check_task is None
        assert not app.state.bridge.inference_tasks
        assert not app.state.bridge.task_pool.tasks
