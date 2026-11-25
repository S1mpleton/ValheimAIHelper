"""Connection checks establish availability, not NPC planning quality."""
import asyncio
import copy
import json

import httpx
import pytest

from test_ollama import MODEL, app_client, plain_reply
from valheim_ai.protocol import Snapshot
from valheim_ai.task_pool import TaskPool


@pytest.mark.parametrize("content", [
    "OK",
    "Связь работает",
    '{"tasks": []}',
    '{"tasks": [{"kind": "collect", "priority": 72}]}',
    '{"steps": [{"action": "invented", "target_id": "unknown"}]}',
])
async def test_any_nonempty_reply_proves_availability_without_task_validation(content):
    generated = []
    def handler(request):
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        body=json.loads(request.content)
        generated.append(body)
        assert "format" not in body and body["options"]["num_predict"]==16
        assert body["options"]["num_ctx"]==1024 and body["think"] is False
        return plain_reply(content)
    async with app_client(handler) as (app, client):
        response = await client.post("/api/check/ollama", json={"model": MODEL})
        assert response.status_code == 200, response.text
        assert response.json()["checks"]==["Модель доступна: OK", "Ответ модели: OK"]
        assert len(generated)==1, "Availability must not trigger schema repair or extra task requests"
        assert not app.state.bridge.task_pool.tasks
        assert app.state.bridge.command.mode == "stop"
        assert app.state.bridge.check_task is None


async def test_check_never_calls_planner_or_pool_and_keeps_private_control_state(monkeypatch, snapshot):
    upstream=[]
    def handler(request):
        upstream.append(request)
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        assert "private-world-character" not in request.content.decode()
        assert "private-user-order" not in request.content.decode()
        assert "private-world" not in request.content.decode()
        return plain_reply('{"tasks":[]}')
    async with app_client(handler) as (app, client):
        state = app.state.bridge
        snapshot.update(task_pool_supported=True, world_id="private-world")
        snapshot["actor"]["name"] = "private-world-character"
        state.last_snapshot = Snapshot.model_validate(snapshot)
        state.task_pool.apply_batch([{"id":"real-follow", "kind":"follow", "priority":17}],
                                   snapshot=state.last_snapshot, source_request="private-user-order")
        state.provider="ollama"; state.model=MODEL
        state.command=state.command.model_copy(update={"mode":"ai", "objective":"private-user-order"})
        before = copy.deepcopy(state.task_pool.status())
        revision, command = state.revision, state.command.model_dump()
        def forbidden(*args, **kwargs):
            raise AssertionError("Connection check must not compile, apply or schedule game tasks")
        monkeypatch.setattr("valheim_ai.task_compiler.compile_tasks", forbidden)
        monkeypatch.setattr(TaskPool, "apply_batch", forbidden)
        monkeypatch.setattr(TaskPool, "next_decision", forbidden)
        response = await client.post("/api/check/ollama", json={"model": MODEL})
        assert response.status_code == 200, response.text
        assert state.task_pool.status() == before
        assert state.revision == revision and state.command.model_dump() == command
        assert state.last_snapshot.actor.name == "private-world-character"
        assert [request.url.path for request in upstream] == ["/api/show", "/api/chat"]
        assert not state.inference_tasks and state.check_task is None


async def test_empty_plain_reply_reports_availability_error_without_task_repair():
    generations=[]
    def handler(request):
        if request.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        generations.append(request)
        return plain_reply("  \n ")
    async with app_client(handler) as (app, client):
        response=await client.post("/api/check/ollama",json={"model":MODEL})
        assert response.status_code==502 and "TaskBatch" not in response.text
        assert "tasks or edits" not in response.text
        assert len(generations)==1 and app.state.bridge.check_task is None
        assert not app.state.bridge.inference_tasks


async def test_stop_cancels_the_single_plain_probe_without_background_repair():
    entered, aborted = asyncio.Event(), asyncio.Event()
    generations = 0
    async def handler(request):
        nonlocal generations
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities":["completion"]})
        generations += 1
        entered.set()
        try: await asyncio.Event().wait()
        finally: aborted.set()
    async with app_client(handler) as (app, client):
        pending = asyncio.create_task(client.post("/api/check/ollama", json={"model":MODEL}))
        await asyncio.wait_for(entered.wait(), 2)
        assert (await client.post("/api/command", json={"mode":"stop"})).status_code == 200
        assert (await asyncio.wait_for(pending, 2)).status_code == 409
        await asyncio.wait_for(aborted.wait(), 2)
        assert generations==1 and app.state.bridge.check_task is None
        assert not app.state.bridge.inference_tasks
        assert not app.state.bridge.task_pool.tasks
