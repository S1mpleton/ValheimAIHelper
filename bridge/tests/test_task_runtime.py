"""Exercise the task-capable mod path, rather than the legacy planner flag."""
import asyncio
import copy

import httpx
import pytest

from valheim_ai.app import create_app
from valheim_ai.protocol import ChatIntent, Decision, Plan, Snapshot, wait
from valheim_ai.task_pool import TaskBatch, TaskEdit, TaskSpec


AUTH = {"Authorization": "Bearer task-runtime-test-token-long-enough"}


def task_world(snapshot):
    value = copy.deepcopy(snapshot)
    value.update(task_pool_supported=True, world_id="world_1", background_planning=True,
                 supported_actions=["wait", "speak", "follow", "pickup", "interact", "give", "drop"])
    value["actor"]["inventory"] = []
    value["entities"] = [
        # Movement tests start outside the native follow restart distance.
        dict(id="leader", name="Человек", kind="player", distance=5, position=dict(x=5, y=0, z=0)),
        dict(id="berry", name="Малина", kind="item", item_prefab="Raspberry", count=3,
             distance=2, position=dict(x=2, y=0, z=0)),
    ]
    return value


@pytest.fixture
async def task_client(tmp_path):
    # Any upstream access is a test failure: compilation is replaced explicitly.
    def unexpected(request):
        raise AssertionError("Unexpected external model call: " + str(request.url))
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as upstream:
        app = create_app(token=AUTH["Authorization"][7:], client=upstream, data_dir=tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=AUTH) as client:
            try:
                yield app.state.bridge, client
            finally:
                await app.state.bridge.tasks.close()


async def connect(client, snapshot):
    response = await client.post("/api/decision", json=snapshot)
    assert response.status_code == 200, response.text
    response = await client.post("/api/provider", json={"provider": "openai", "model": "test", "api_key": "mock-secret"})
    assert response.status_code == 200, response.text


async def activate_prior_ai_plan(state, client, observed):
    # The fixture already has a model-authored plan. Starting/resuming it must
    # not make runtime invent a default follow goal or choose its importance.
    state.tasks.pool.apply_batch([TaskSpec(kind="follow", priority=41)], snapshot=Snapshot.model_validate(observed))
    result = await client.post("/api/command", json={"mode":"ai"})
    assert result.status_code == 200, result.text


async def poll(client, snapshot, **feedback):
    snapshot["sequence"] += 1
    snapshot.update(feedback)
    response = await client.post("/api/decision", json=snapshot)
    assert response.status_code == 200, response.text
    return response.json()


async def finish_model(state):
    task = state.tasks.task
    if task is not None:
        await asyncio.wait_for(task, 2)


def control_body(state, action, **fields):
    return dict(action=action, revision=state.tasks.pool.revision,
                world_id=state.last_snapshot.world_id, actor_id=state.last_snapshot.actor.id, **fields)


async def test_compound_request_compiles_once_and_native_scheduler_catches_up_then_resumes(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    calls = []
    async def compiler(_state, objective, _snapshot, chat):
        calls.append(objective)
        return TaskBatch(tasks=[TaskSpec(kind="follow", priority=30),
            TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, near_leader=True, priority=60)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    assert (await client.post("/api/command", json={"mode": "ai", "objective": "Гриша, следуй за мной и собирай малину"})).status_code == 200
    await poll(client, observed)
    await finish_model(state)
    first = await poll(client, observed)
    assert first["action"] == "pickup" and first["target_id"] == "berry"
    again = await poll(client, observed, plan_id=first["plan_id"], action_status="running")
    assert again["plan_id"] == first["plan_id"]
    observed["entities"][0].update(distance=20, position=dict(x=20, y=0, z=0))
    catch_up = await poll(client, observed)
    assert catch_up["action"] == "follow"
    assert next(n for n in state.tasks.pool.tasks if n.kind == "collect").status == "paused"
    observed["entities"][0].update(distance=5, position=dict(x=5, y=0, z=0))
    resumed = await poll(client, observed)
    assert resumed["action"] == "pickup" and resumed["plan_id"] != first["plan_id"]
    observed["actor"]["inventory"] = [dict(id="bag:berries", name="Малина", item_prefab="Raspberry", count=3, equipped=False, food=True, equipable=False)]
    observed["entities"].pop()
    returned = await poll(client, observed, plan_id=resumed["plan_id"], action_status="success")
    assert returned["action"] == "follow"
    assert next(n for n in state.tasks.pool.tasks if n.kind == "collect").count == 3
    assert len(calls) == 1


async def test_high_priority_follow_yields_to_collect_when_already_near_and_resumes_when_far(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    observed["entities"][0].update(distance=2, position=dict(x=2, y=0, z=0))
    async def compiler(*_):
        return TaskBatch(tasks=[TaskSpec(kind="follow", priority=90),
            TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, near_leader=True, priority=80)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await client.post("/api/command", json={"mode":"ai", "objective":"Следуй и собирай малину по пути"})
    await poll(client, observed)
    await finish_model(state)
    gathering = await poll(client, observed)
    assert gathering["action"] == "pickup" and gathering["target_id"] == "berry"
    assert {node.kind:node.priority for node in state.tasks.pool.tasks} == {"follow":90, "collect":80}
    # Confirm the pickup before asking a movement task to take over its lease.
    observed["actor"]["inventory"] = [dict(id="bag:berries", name="Малина", item_prefab="Raspberry", count=3, equipped=False, food=True, equipable=False)]
    observed["entities"].pop()
    observed["entities"][0].update(distance=5, position=dict(x=5, y=0, z=0))
    following = await poll(client, observed, plan_id=gathering["plan_id"], action_status="success", action_completed_amount=3)
    assert following["action"] == "follow"
    # Approaching the leader does not complete/cancel the standing follow task.
    observed["entities"][0].update(distance=2, position=dict(x=2, y=0, z=0))
    idle = await poll(client, observed, plan_id=following["plan_id"], action_status="running")
    assert idle["action"] == "wait"
    assert {node.kind:node.priority for node in state.tasks.pool.tasks} == {"follow":90, "collect":80}
    assert not state.tasks.pool.history


async def test_collect_then_give_dependency_waits_for_actual_inventory_and_give_receipt(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    async def compiler(*_):
        return TaskBatch(tasks=[TaskSpec(id="collect", kind="collect", item_prefabs=["Raspberry"], amount=5),
            TaskSpec(kind="give", item_prefabs=["Raspberry"], amount=5, depends_on=["collect"])])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "ai", "objective": "Собери пять ягод и отдай мне"})
    await poll(client, observed)
    await finish_model(state)
    pickup = await poll(client, observed)
    assert pickup["action"] == "pickup"
    # A transport success without stock is not proof that the requested count exists.
    await poll(client, observed, plan_id=pickup["plan_id"], action_status="success")
    assert not any(n.kind == "collect" and n.status == "completed" for n in state.tasks.pool.history)
    observed["actor"]["inventory"] = [dict(id="bag:berries", name="Малина", item_prefab="Raspberry", count=5, equipped=False, food=True, equipable=False)]
    observed["entities"].pop()
    giving = await poll(client, observed)
    assert giving["action"] == "give" and giving["amount"] == 5
    assert any(n.kind == "collect" and n.status == "completed" for n in state.tasks.pool.history)
    assert any(n.kind == "give" for n in state.tasks.pool.tasks)
    observed["actor"]["inventory"] = []
    await poll(client, observed, plan_id=giving["plan_id"], action_status="success", action_completed_amount=5)
    assert not state.tasks.pool.tasks
    assert next(n for n in state.tasks.pool.history if n.kind == "give").completed_amount == 5


async def test_ui_edits_require_current_pool_revision_world_and_actor(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "follow"})
    node = state.tasks.pool.tasks[0]
    request = control_body(state, "priority", task_id=node.id, priority=75)
    for changed in ({"world_id": "another_world"}, {"actor_id": "another_npc"}, {"revision": request["revision"] - 1}):
        assert (await client.post("/api/tasks/control", json={**request, **changed})).status_code == 409
    assert node.priority == 50
    response = await client.post("/api/tasks/control", json=request)
    assert response.status_code == 200 and response.json()["revision"] == state.revision
    assert node.priority == 75
    assert (await client.post("/api/tasks/control", json=request)).status_code == 409
    assert (await client.post("/api/tasks/control", json=control_body(state, "pause", task_id=node.id))).status_code == 200
    assert node.manual_paused
    assert (await poll(client, observed))["action"] == "wait"
    assert (await client.post("/api/tasks/control", json=control_body(state, "resume", task_id=node.id))).status_code == 200
    assert (await poll(client, observed))["action"] == "follow"
    assert (await client.post("/api/tasks/control", json=control_body(state, "cancel", task_id=node.id))).status_code == 200
    assert not state.tasks.pool.tasks and state.tasks.pool.history[-1].status == "cancelled"


async def test_stop_cancels_inflight_decomposition_and_never_applies_late_answer(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def compiler(*_):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "ai", "objective": "Собирай малину"})
    await poll(client, observed)
    await asyncio.wait_for(entered.wait(), 1)
    running = state.tasks.task
    assert (await client.post("/api/command", json={"mode": "stop"})).status_code == 200
    await asyncio.gather(running, return_exceptions=True)
    assert cancelled.is_set() and state.tasks.task is None and state.tasks.pending is None
    assert not state.tasks.pool.tasks
    assert (await poll(client, observed))["action"] == "wait"


async def test_inventory_pause_preserves_pool_and_plain_stop_cancels_it(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "follow"})
    task_id = state.tasks.pool.tasks[0].id
    await client.post("/api/command", json={"mode": "stop", "pause_tasks": True})
    assert state.tasks.pool.paused and state.tasks.pool.tasks[0].id == task_id
    assert (await poll(client, observed))["action"] == "wait"
    await client.post("/api/tasks/control", json=control_body(state, "resume"))
    assert (await poll(client, observed))["action"] == "follow"
    await client.post("/api/command", json={"mode": "stop"})
    assert not state.tasks.pool.tasks and state.tasks.pool.history[-1].id == task_id


async def test_world_persistence_restores_paused_and_context_cannot_interrupt_live_control(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "follow"})
    task_id = state.tasks.pool.tasks[0].id
    other = copy.deepcopy(observed)
    other.update(world_id="world_2", session_id="context_only")
    rejected = await client.post("/api/tasks/context", json=other)
    assert rejected.status_code == 409
    assert state.tasks.world == ("world_1", observed["actor"]["id"])
    await client.post("/api/command", json={"mode": "stop", "pause_tasks": True})
    command_revision = state.revision
    assert (await client.post("/api/tasks/context", json=other)).status_code == 200
    assert not state.tasks.pool.tasks and state.revision == command_revision
    assert (await client.post("/api/tasks/context", json=observed)).status_code == 200
    assert state.tasks.pool.paused and state.tasks.pool.tasks[0].id == task_id
    assert state.command.mode == "stop" and "паузе" in state.tasks.notice
    assert (await poll(client, observed))["action"] == "wait"
    await client.post("/api/tasks/control", json=control_body(state, "resume"))
    assert (await poll(client, observed))["action"] == "follow"


async def test_rejected_other_character_observation_does_not_replace_pool(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "follow"})
    task_id = state.tasks.pool.tasks[0].id
    wrong = copy.deepcopy(observed)
    wrong.update(session_id="intruding_session", world_id="another_world")
    wrong["actor"]["id"] = "another_npc"
    response = await client.post("/api/decision", json=wrong)
    assert response.status_code == 409
    assert state.tasks.world == ("world_1", observed["actor"]["id"])
    assert state.tasks.pool.tasks[0].id == task_id


async def test_rejected_proposal_restores_work_and_successful_append_keeps_manual_pause(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "follow"})
    following = state.tasks.pool.tasks[0]
    waiting = state.tasks.pool.apply_batch([TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True)])[0]
    state.tasks.pool.control("pause", waiting.id)
    async def invalid(*_):
        raise ValueError("План не прошёл проверку")
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", invalid)
    await client.post("/api/command", json={"mode": "ai", "objective": "Добавь ещё поручение"})
    await poll(client, observed)
    await finish_model(state)
    assert not state.tasks.pool.paused and waiting.manual_paused
    assert {n.id for n in state.tasks.pool.tasks} == {following.id, waiting.id}
    assert (await poll(client, observed))["action"] == "follow"
    async def valid(*_):
        return TaskBatch(tasks=[TaskSpec(kind="goal", objective="Другое поручение", priority=10)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", valid)
    await client.post("/api/command", json={"mode": "ai", "objective": "Запомни другое поручение"})
    await poll(client, observed)
    await finish_model(state)
    await poll(client, observed)
    assert waiting.manual_paused and waiting.status == "paused" and len(state.tasks.pool.tasks) == 3
    assert (await poll(client, observed))["action"] == "follow"


@pytest.mark.parametrize("action", ["give", "drop"])
async def test_generic_goal_cannot_bypass_typed_item_transfer_validation(task_client, snapshot, monkeypatch, action):
    state, client = task_client
    observed = task_world(snapshot)
    observed["actor"]["inventory"] = [dict(id="bag:berries", name="Малина", item_prefab="Raspberry", count=5, equipped=False, food=True, equipable=False)]
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    node = state.tasks.pool.apply_batch([TaskSpec(kind="goal", objective="Отдай весь инвентарь", priority=90)], source_request="Дай пять ягод")[0]
    async def decide(*_, **__):
        return Plan(steps=[Decision(action=action, target_id="leader" if action == "give" else "", selection="all_inventory", duration=1)])
    monkeypatch.setattr(state, "decide", decide)
    await poll(client, observed)
    await finish_model(state)
    assert state.tasks.pool.get(node.id) is None
    assert next(n for n in state.tasks.pool.history if n.id == node.id).status == "failed"
    assert not state.tasks.goal_steps
    assert (await poll(client, observed))["action"] not in ("give", "drop")


async def test_chat_classification_uses_original_request_and_preserves_other_pending_messages(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    for text in ("Собери пять ягод", "И потом собери камни"):
        response = await client.post("/api/chat", json={"speaker": "Лидер", "actor_name": "Гриша", "text": text, "explicit_address": False})
        assert response.json()["queued"]
    first_id, second_id = [m["id"] for m in state.pending_chat]
    async def decide(*_, **__):
        return ChatIntent(message_id=first_id, addressed=True, mode="task", objective="Отдай весь инвентарь", reply="")
    monkeypatch.setattr("valheim_ai.task_runtime.classify_chat", decide)
    await poll(client, observed)
    await finish_model(state)
    assert state.tasks.pending[0] == "Собери пять ягод"
    assert state.command.objective == "Собери пять ягод"
    assert [m["id"] for m in state.pending_chat] == [second_id]


async def test_model_follow_synonym_adds_follow_without_erasing_gathering(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    gathering = state.tasks.pool.apply_batch([TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True)])[0]
    await client.post("/api/chat", json={"speaker": "Лидер", "actor_name": "Гриша", "text": "Гриша, пошли", "explicit_address": False})
    message_id = state.pending_chat[-1]["id"]
    async def decide(*_, **__):
        return ChatIntent(message_id=message_id, addressed=True, mode="follow", objective="", reply="")
    monkeypatch.setattr("valheim_ai.task_runtime.classify_chat", decide)
    await poll(client, observed)
    await finish_model(state)
    assert gathering.id in state.tasks.pool.nodes and len(state.tasks.pool.tasks) == 2
    assert sum(n.kind == "follow" for n in state.tasks.pool.tasks) == 1


async def test_delayed_snapshot_cannot_change_task_scope_or_revision(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await client.post("/api/command", json={"mode": "follow"})
    await poll(client, observed)
    expected = state.tasks.status()
    revision = state.revision
    for changes in ({}, {"world_id": "other_world"}):
        delayed = {**copy.deepcopy(observed), **changes, "sequence": 1}
        response = await client.post("/api/decision", json=delayed)
        assert response.status_code == 409
        assert state.tasks.status() == expected and state.revision == revision


async def test_disabling_chat_discards_inflight_classification(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Собери малину"})
    message_id = state.pending_chat[-1]["id"]
    entered, release = asyncio.Event(), asyncio.Event()
    async def decide(*_, **__):
        entered.set()
        await release.wait()
        return ChatIntent(message_id=message_id, addressed=True, mode="task", objective="Собери малину", reply="")
    monkeypatch.setattr("valheim_ai.task_runtime.classify_chat", decide)
    await poll(client, observed)
    await asyncio.wait_for(entered.wait(), 1)
    await poll(client, observed, listen_chat=False)
    release.set()
    await finish_model(state)
    assert state.tasks.pending is None and not state.pending_chat
    assert len(state.tasks.pool.tasks) == 1 and state.tasks.pool.tasks[0].kind == "follow"


async def test_explicit_request_keeps_following_with_same_native_identity_until_new_plan_is_ready(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    entered, release = asyncio.Event(), asyncio.Event()
    async def compiler(*_):
        entered.set()
        await release.wait()
        return TaskBatch(tasks=[TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=70)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    original = await poll(client, observed)
    assert original["action"] == "follow"
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Гриша, собирай малину", "explicit_address":True})
    for _ in range(3):
        continuing = await poll(client, observed)
        assert (continuing["action"], continuing["plan_id"], continuing["command_revision"], continuing["mode"]) == (
            original["action"], original["plan_id"], original["command_revision"], original["mode"])
        assert not state.tasks.pool.paused
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await finish_model(state)
    assert state.tasks.pending_batch is not None
    switched = await poll(client, observed)
    assert switched["action"] == "pickup" and switched["target_id"] == "berry"
    assert switched["command_revision"] == original["command_revision"] + 1


async def test_ready_replacement_waits_for_inventory_receipt_without_reissuing_quantity(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    observed["actor"]["inventory"] = [dict(id="bag:berries", name="Малина", item_prefab="Raspberry", count=7, equipped=False, food=True, equipable=False)]
    async def compiler(*_):
        return TaskBatch(mode="replace", tasks=[TaskSpec(kind="follow", priority=30)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    state.tasks.pool.apply_batch([TaskSpec(kind="give", item_prefabs=["Raspberry"], amount=5)], mode="replace")
    issued = await poll(client, observed)
    assert issued["action"] == "give" and issued["amount"] == 5
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Гриша, теперь только следуй", "explicit_address":True})
    running = await poll(client, observed, plan_id=issued["plan_id"], action_status="running")
    await finish_model(state)
    waiting = await poll(client, observed)
    for result in (running, waiting):
        assert (result["action"], result["plan_id"], result["command_revision"], result["amount"]) == (
            "give", issued["plan_id"], issued["command_revision"], 5)
    assert state.tasks.pending_batch is not None
    observed["actor"]["inventory"][0]["count"] = 2
    next_action = await poll(client, observed, plan_id=issued["plan_id"], action_status="success", action_completed_amount=5)
    assert next_action["action"] == "follow"
    assert next_action["command_revision"] == issued["command_revision"] + 1
    delivered = [node for node in state.tasks.pool.history if node.kind == "give"]
    assert len(delivered) == 1 and delivered[0].status == "completed" and delivered[0].completed_amount == 5


async def test_rejected_ready_batch_leaves_previous_native_lease_unchanged(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    async def compiler(*_):
        return TaskBatch(tasks=[TaskSpec(kind="goal", objective="Недопустимый граф", depends_on=["missing"])])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    issued = await poll(client, observed)
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Гриша, другое поручение", "explicit_address":True})
    await poll(client, observed)
    await finish_model(state)
    after = await poll(client, observed)
    assert (after["action"], after["plan_id"], after["command_revision"]) == ("follow", issued["plan_id"], issued["command_revision"])
    assert state.tasks.pending is None and state.tasks.pending_batch is None
    assert len(state.tasks.pool.tasks) == 1


async def test_api_failure_during_new_request_preserves_native_work(task_client, snapshot, monkeypatch):
    from valheim_ai.providers import ProviderRateLimited
    state, client = task_client
    observed = task_world(snapshot)
    async def compiler(*_):
        raise ProviderRateLimited("Закончилась квота")
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    issued = await poll(client, observed)
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Гриша, новое поручение", "explicit_address":True})
    await poll(client, observed)
    await finish_model(state)
    after = await poll(client, observed)
    assert (after["action"], after["plan_id"], after["command_revision"]) == ("follow", issued["plan_id"], issued["command_revision"])
    assert state.command.mode == "ai" and state.provider_error is not None


async def test_stop_after_compilation_cancels_uncommitted_batch(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    async def compiler(*_):
        return TaskBatch(tasks=[TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await client.post("/api/command", json={"mode":"ai", "objective":"Собирай малину"})
    await poll(client, observed)
    await finish_model(state)
    assert state.tasks.pending_batch is not None
    await client.post("/api/command", json={"mode":"stop"})
    assert state.tasks.pending_batch is None
    assert (await poll(client, observed))["action"] == "wait"
    assert not state.tasks.pool.tasks


async def test_cancellation_only_proposal_removes_standing_collection_after_processing(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    collecting = state.tasks.pool.apply_batch([TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=70)])[0]
    async def compiler(*_):
        return TaskBatch(tasks=[], edits=[TaskEdit(action="cancel", task_id=collecting.id)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    issued = await poll(client, observed)
    assert issued["action"] == "pickup"
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Гриша, больше не собирай малину", "explicit_address":True})
    pending = await poll(client, observed)
    assert pending["action"] == "pickup" and pending["plan_id"] == issued["plan_id"]
    await finish_model(state)
    after = await poll(client, observed)
    assert after["action"] == "follow" and state.tasks.pool.get(collecting.id) is None
    assert next(node for node in state.tasks.pool.history if node.id == collecting.id).status == "cancelled"


async def test_follow_mode_does_not_change_while_an_ai_request_is_pending(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    entered, release = asyncio.Event(), asyncio.Event()
    async def compiler(*_):
        entered.set()
        await release.wait()
        return TaskBatch(tasks=[TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=70)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await client.post("/api/command", json={"mode":"follow"})
    issued = await poll(client, observed)
    response = await client.post("/api/command", json={"mode":"ai", "objective":"Собирай малину"})
    assert response.status_code == 200
    pending = await poll(client, observed)
    assert (pending["mode"], pending["action"], pending["plan_id"], pending["command_revision"]) == (
        "follow", "follow", issued["plan_id"], issued["command_revision"])
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await finish_model(state)
    after = await poll(client, observed)
    assert after["mode"] == "ai" and after["action"] == "pickup"



async def test_empty_ai_start_waits_for_model_goals_and_keeps_model_priorities(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    entered, release = asyncio.Event(), asyncio.Event()
    async def compiler(*_):
        entered.set()
        await release.wait()
        return TaskBatch(tasks=[TaskSpec(kind="follow", priority=90),
            TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=13)])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await connect(client, observed)
    await client.post("/api/command", json={"mode":"ai"})
    assert not state.tasks.pool.tasks and state.tasks.pending is not None
    assert (await poll(client, observed))["action"] == "wait"
    await asyncio.wait_for(entered.wait(), 1)
    assert not state.tasks.pool.tasks
    release.set()
    await finish_model(state)
    assert not state.tasks.pool.tasks
    action = await poll(client, observed)
    assert action["action"] == "follow"
    assert {node.kind:node.priority for node in state.tasks.pool.tasks} == {"follow":90, "collect":13}


async def test_copied_existing_plan_reports_no_change_and_preserves_motor_identity(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    gathering = state.tasks.pool.apply_batch([TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=61)],
        snapshot=Snapshot.model_validate(observed), source_request="Собирай малину")[0]
    issued = await poll(client, observed)
    assert issued["action"] == "pickup"
    async def compiler(*_):
        return TaskBatch(tasks=[TaskSpec(kind="follow", priority=41), *[
            TaskSpec(kind="collect", title="Малина по пути", item_prefabs=["Raspberry"], continuous=True, priority=61)
            for _ in range(14)]])
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    await client.post("/api/chat", json={"speaker":"Лидер", "actor_name":"Гриша", "text":"Гриша, возьми топор", "explicit_address":True})
    await poll(client, observed)
    await finish_model(state)
    unchanged = await poll(client, observed)
    identity = lambda reply: tuple(reply[key] for key in ("action", "plan_id", "command_revision", "mode"))
    assert identity(unchanged) == identity(issued)
    assert len(state.tasks.pool.tasks) == 2 and gathering.source_request == "Собирай малину"
    assert "модель не предложила изменений" in state.tasks.notice
    assert state.tasks.pending is None and state.tasks.pending_batch is None


async def test_private_dialogue_without_name_bypasses_addressee_model_and_keeps_old_work(task_client, snapshot, monkeypatch):
    state, client = task_client
    observed = task_world(snapshot)
    await connect(client, observed)
    await activate_prior_ai_plan(state, client, observed)
    issued = await poll(client, observed)
    entered, release = asyncio.Event(), asyncio.Event()
    async def compiler(_state, objective, *_):
        assert objective == "подбери малину"
        entered.set()
        await release.wait()
        return TaskBatch(tasks=[TaskSpec(kind="collect", item_prefabs=["Raspberry"], priority=73)])
    async def forbidden(*_):
        raise AssertionError("Private dialogue must not guess the addressee")
    monkeypatch.setattr("valheim_ai.task_runtime.compile_tasks", compiler)
    monkeypatch.setattr("valheim_ai.task_runtime.classify_chat", forbidden)
    response = await client.post("/api/chat", json={"speaker":"Гоша", "actor_name":"Гоша", "text":"подбери малину", "direct_dialogue":True})
    assert response.json()["queued"] and not state.pending_chat
    continuing = await poll(client, observed)
    assert (continuing["action"], continuing["plan_id"], continuing["command_revision"]) == (
        issued["action"], issued["plan_id"], issued["command_revision"])
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await finish_model(state)
    assert (await poll(client, observed))["action"] == "pickup"
