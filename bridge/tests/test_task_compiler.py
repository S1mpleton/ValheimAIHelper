import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from valheim_ai.dialogue import Dialogue
from valheim_ai.ollama import ModelSession
from valheim_ai.performance import Performance
from valheim_ai.protocol import Snapshot
from valheim_ai.providers import ProviderInvalidAction, ProviderUnavailable
from valheim_ai.task_compiler import compile_tasks, parse_tasks, task_request
from valheim_ai.task_pool import TaskBatch, TaskPool, TaskSpec


def world(snapshot):
    snapshot.update(item_catalog=[
        {"item_prefab": "Raspberry", "name": "Малина", "token": "$item_raspberry", "category": "Consumable"},
        {"item_prefab": "AxeStone", "name": "Каменный топор", "token": "$item_axe_stone", "category": "OneHandedWeapon"},
        {"item_prefab": "Stone", "name": "Камень", "token": "$item_stone", "category": "Material"},
    ], supported_actions=["wait", "speak", "follow", "pickup", "give", "drop", "eat", "equip"])
    return Snapshot.model_validate(snapshot)


def state_for(client, provider="ollama"):
    return SimpleNamespace(
        provider=provider, model={"ollama": "local-agent:4b", "openai": "gpt-6-luna", "gemini": "gemini-2.5-flash"}[provider],
        ollama_url="http://127.0.0.1:11434", keys={"openai": "cloud-openai-secret", "gemini": "cloud-gemini-secret"},
        inference_tasks=set(), performance=Performance(), dialogue=Dialogue(lambda: ["cloud-openai-secret", "cloud-gemini-secret"]),
        ollama_session=ModelSession(lambda *_: None), client_for=lambda _: client,
        decision_timeout=10, task_pool=TaskPool(),
    )


def task_properties(schema, kind="follow"):
    spec = schema["$defs"]["TaskSpec"]
    if "properties" in spec: return spec["properties"]
    return next(branch["properties"] for branch in spec["anyOf"] if branch["properties"]["kind"]["const"] == kind)


def model_task(schema, kind, index=0, **fields):
    task = dict(kind=kind, item_prefabs=[], amount=1, selection="exact",
                continuous=kind == "follow", near_leader=False, objective="", priority=30 if kind == "follow" else 60 if kind == "collect" else 75)
    if "id" in task_properties(schema): task["id"] = task_properties(schema)["id"]["enum"][index]
    task.update(fields)
    return task


def model_batch(schema):
    return {"mode": "append", "tasks": [
        model_task(schema, "follow"),
        model_task(schema, "collect", index=1, item_prefabs=["Raspberry"], continuous=True, near_leader=True),
    ]}


def test_request_exposes_observed_types_and_editable_jobs_as_data(snapshot):
    observed = world(snapshot)
    existing = [TaskSpec(id="earlier", kind="collect", item_prefabs=["Stone"], amount=5, priority=23)]
    request = task_request("Пошли и собирай малину по дороге", observed, existing_tasks=existing, batch_id="test", combat_policy="avoid")
    payload = json.loads(request["messages"][-1]["content"])
    assert payload["latest_request"] == "Пошли и собирай малину по дороге"
    assert payload["current_combat_policy"] == "avoid"
    assert payload["existing_tasks"][0]["id"] == "earlier"
    assert payload["existing_tasks"][0]["priority"] == 23
    assert "Raspberry" in {i["item_prefab"] for i in payload["item_type_catalog"]}
    assert "AxeStone" not in {i["item_prefab"] for i in payload["item_type_catalog"]}
    assert request["format"]["properties"]["tasks"]["maxItems"] == 15
    assert request["format"]["$defs"]["TaskEdit"]["properties"]["task_id"]["enum"] == ["earlier"]


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
def test_full_planner_has_required_model_priority_and_optional_native_ids(snapshot, provider):
    request = task_request("Собери камни, затем сделай инструмент", world(snapshot), provider=provider)
    spec = request["format"]["$defs"]["TaskSpec"]
    props = spec["properties"]
    assert {"kind", "priority", "depends_on", "action", "objective", "item_prefabs"}.issubset(props)
    assert "priority" in spec["required"]
    if provider == "ollama": assert "id" not in spec["required"]
    assert spec["additionalProperties"] is False
    assert {"follow", "collect", "give", "drop", "action", "goal"} == set(props["kind"]["enum"])
    raw = {"tasks":[{"kind":"collect", "item_prefabs":["Stone"], "priority":19}]}
    assert parse_tasks(raw, world(snapshot), require_priority=True).tasks[0].priority == 19
    raw["tasks"][0].pop("priority")
    with pytest.raises(ProviderInvalidAction, match="приоритет"):
        parse_tasks(raw, world(snapshot), require_priority=True)


def test_whole_bag_is_validated_after_compact_semantic_output(snapshot):
    observed = world(snapshot)
    valid = {"tasks": [{"kind":"give", "item_prefabs":[], "amount":1, "selection":"all_inventory"}]}
    assert parse_tasks(valid, observed).tasks[0].selection == "all_inventory"
    for changes in ({"amount":5}, {"item_prefabs":["Stone"]}):
        with pytest.raises(ValueError): parse_tasks({"tasks":[{**valid["tasks"][0], **changes}]}, observed)


def test_live_request_contains_no_copyable_demo_or_old_mission_title(snapshot):
    observed = world(snapshot)
    existing = [TaskSpec(id="saved_job_1234567890", kind="collect", title="Малина по пути", item_prefabs=["Raspberry"], continuous=True)]
    request = task_request("Возьми каменный топор", observed, existing_tasks=existing)
    messages = request["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    facts = json.loads(messages[-1]["content"])
    assert facts["latest_request"] == "Возьми каменный топор"
    assert "Малина по пути" not in json.dumps(messages, ensure_ascii=False)
    assert facts["existing_tasks"][0]["id"] == existing[0].id
    assert all("title" not in row for row in facts["existing_tasks"])
    assert all("count" not in item and "id" not in item for item in facts["item_type_catalog"])
    assert "format_examples_not_additional_requests" not in facts


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_cloud_strict_schema_keeps_flat_required_object(provider, snapshot):
    schema = task_request("Пошли", world(snapshot), provider=provider)["format"]
    spec = schema["$defs"]["TaskSpec"]
    assert "anyOf" not in spec and set(spec["required"]) == set(spec["properties"])
    assert spec["additionalProperties"] is False


def test_empty_catalog_requires_clarification_instead_of_unknown_collection(snapshot):
    observed = Snapshot.model_validate({**snapshot, "leader_id": "", "entities": [], "actor": {**snapshot["actor"], "inventory": []}})
    request = task_request("Подбери неясный предмет", observed)
    props = task_properties(request["format"])
    assert props["item_prefabs"]["maxItems"] == 0
    with pytest.raises(ValueError): parse_tasks({"tasks":[{"kind":"collect", "item_prefabs":[]}]}, observed)
    with pytest.raises(ValueError): parse_tasks({"tasks":[{"kind":"collect", "item_prefabs":["Fake"]}]}, observed)
    assert len(request["messages"]) == 2


def test_quantity_delivery_is_dependent_and_pool_transaction_rejects_cycles(snapshot):
    observed = world(snapshot)
    raw = {"mode": "append", "tasks": [
        {"id": "collect", "kind": "collect", "item_prefabs": ["Stone"], "amount": 5},
        {"id": "deliver", "kind": "give", "item_prefabs": ["Stone"], "amount": 5, "depends_on": ["collect"]},
    ]}
    batch = parse_tasks(raw, observed)
    assert isinstance(batch, TaskBatch) and batch.tasks[1].amount == 5
    pool = TaskPool()
    pool.apply_batch(batch, snapshot=observed)
    assert len(pool.tasks) == 2
    before = [t.model_dump() for t in pool.tasks]
    cyclic = {"tasks": [
        {"id": "a", "kind": "goal", "objective": "Наруби дерево", "depends_on": ["b"]},
        {"id": "b", "kind": "goal", "objective": "Сделай дубину", "depends_on": ["a"]},
    ]}
    with pytest.raises(ValueError):
        pool.apply_batch(parse_tasks(cyclic, observed), snapshot=observed)
    assert [t.model_dump() for t in pool.tasks] == before


def test_continuous_collection_along_leader_path_runs_parallel_to_endless_follow(snapshot):
    observed = world(snapshot)
    observed.entities[-1] = observed.entities[-1].model_copy(update={"item_prefab": "Raspberry", "name": "Малина"})
    raw = {"tasks": [
        {"id": "follow", "kind": "follow", "priority": 30},
        {"id": "berries", "kind": "collect", "item_prefabs": ["Raspberry"], "priority": 60,
         "continuous": True, "near_leader": True, "depends_on": ["follow"]},
    ]}
    batch = parse_tasks(raw, observed)
    assert batch.tasks[1].depends_on == ["follow"]
    assert raw["tasks"][1]["depends_on"] == ["follow"]
    with pytest.raises(ValueError, match="continuous"):
        TaskPool().apply_batch(batch, snapshot=observed)


def test_existing_continuous_follow_is_not_a_prerequisite_for_along_path_gathering(snapshot):
    observed = world(snapshot)
    following = TaskSpec(id="existing_follow", kind="follow")
    batch = parse_tasks({"tasks": [{"kind": "collect", "item_prefabs": ["Raspberry"], "continuous": True,
        "near_leader": True, "depends_on": [following.id]}]}, observed, existing_tasks=[following])
    assert batch.tasks[0].depends_on == [following.id]


@pytest.mark.parametrize("continuous,near_leader", [(False, True), (True, False)])
def test_other_collection_dependencies_are_not_rewritten(snapshot, continuous, near_leader):
    batch = parse_tasks({"tasks": [
        {"id": "follow", "kind": "follow"},
        {"kind": "collect", "item_prefabs": ["Raspberry"], "continuous": continuous,
         "near_leader": near_leader, "depends_on": ["follow"]},
    ]}, world(snapshot))
    assert batch.tasks[1].depends_on == ["follow"]
    with pytest.raises(ValueError, match="continuous"):
        TaskPool().apply_batch(batch)


def test_parallel_follow_repair_keeps_real_prerequisites_and_other_endless_dependencies(snapshot):
    batch = parse_tasks({"tasks": [
        {"id": "follow", "kind": "follow"},
        {"id": "other", "kind": "goal", "objective": "Охраняй дом", "continuous": True},
        {"kind": "collect", "item_prefabs": ["Raspberry"], "continuous": True,
         "near_leader": True, "depends_on": ["follow", "other"]},
    ]}, world(snapshot))
    assert batch.tasks[2].depends_on == ["follow", "other"]
    with pytest.raises(ValueError, match="continuous"):
        TaskPool().apply_batch(batch)


def test_omitted_compact_combat_policy_preserves_avoid_but_explicit_change_is_allowed(snapshot):
    observed = world(snapshot)
    raw = {"tasks": [{"kind": "follow"}]}
    assert parse_tasks(raw, observed, combat_policy="avoid").combat_policy == "avoid"
    assert parse_tasks({**raw, "combat_policy": "defend"}, observed, combat_policy="avoid").combat_policy == "defend"


@pytest.mark.parametrize("raw", [
    {"tasks": [{"kind": "collect", "item_prefabs": ["InventedAxe"]}]},
    {"tasks": [{"kind": "follow", "priority": 100}]},
    {"tasks": [{"kind": "give", "item_prefabs": ["Stone"], "amount": 0}]},
    {"tasks": [{"kind": "action", "action": {"action": "give", "target_id": "leader", "item_id": "future-inventory-id", "amount": 5, "duration": 1, "say": "Передам"}}]},
    {"tasks": [{"kind": "follow"} for _ in range(16)]},
])
def test_unknown_item_reserved_priority_impossible_action_and_overflow_are_not_compiled(snapshot, raw):
    with pytest.raises(ValueError):
        parse_tasks(raw, world(snapshot))


async def test_ollama_compiles_one_call_without_thinking_or_applying_pool(snapshot):
    calls = []
    def handler(request):
        assert "authorization" not in request.headers
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        body = json.loads(request.content)
        calls.append(body)
        assert body["think"] is False and body["format"]["title"] == "TaskBatch"
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(model_batch(body["format"]))}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client)
        batch = await compile_tasks(state, "Следуй и собирай малину", world(snapshot))
        assert [t.kind for t in batch.tasks] == ["follow", "collect"]
        assert batch.tasks[1].near_leader and batch.tasks[1].priority > batch.tasks[0].priority
        assert len(calls) == 1 and not state.task_pool.tasks and not state.inference_tasks
        record = next(iter(state.dialogue.records.values()))
        assert record.purpose == "tasks" and record.status == "ok"
        assert "не исполнено" in record.result
        assert state.performance.last["purpose"] == "tasks"


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_cloud_uses_strict_task_schema_low_reasoning_and_masks_credentials(snapshot, provider):
    def handler(request):
        body = json.loads(request.content)
        if provider == "openai":
            assert request.headers["authorization"] == "Bearer cloud-openai-secret"
            assert body["reasoning_effort"] == "low"
            schema = body["response_format"]["json_schema"]["schema"]
            result = model_batch(schema)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
        assert request.headers["x-goog-api-key"] == "cloud-gemini-secret"
        assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
        schema = body["generationConfig"]["responseJsonSchema"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"thought": True, "text": "never parse reasoning"}, {"text": json.dumps(model_batch(schema))},
        ]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        result = await compile_tasks(state, "Иди за мной и собирай малину", world(snapshot))
        assert len(result.tasks) == 2
        record = next(iter(state.dialogue.records.values()))
        assert record.status == "ok" and "cloud-openai-secret" not in record.request and "cloud-gemini-secret" not in record.request


async def test_cancellation_finishes_trace_and_leaves_existing_pool_unchanged(snapshot):
    entered = asyncio.Event()
    async def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={})
        entered.set()
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client)
        state.task_pool.apply_batch([TaskSpec(kind="follow")])
        before = [t.id for t in state.task_pool.tasks]
        task = asyncio.create_task(compile_tasks(state, "Собирай малину", world(snapshot)))
        await asyncio.wait_for(entered.wait(), 1)
        assert task in state.inference_tasks
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert [t.id for t in state.task_pool.tasks] == before and not state.inference_tasks
        assert next(iter(state.dialogue.records.values())).status == "cancelled"


async def test_invalid_model_batch_does_not_mutate_existing_tasks(snapshot):
    def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={})
        return httpx.Response(200, json={"done": True, "message": {"content": '{"tasks":[{"kind":"collect","item_prefabs":["FakeItem"]}]}'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client)
        state.task_pool.apply_batch([TaskSpec(kind="follow")])
        before = [t.id for t in state.task_pool.tasks]
        with pytest.raises(ProviderInvalidAction):
            await compile_tasks(state, "Подбери несуществующий предмет", world(snapshot))
        assert [t.id for t in state.task_pool.tasks] == before
        assert next(iter(state.dialogue.records.values())).status == "error"


async def test_compiler_timeout_releases_inference_tracking(snapshot):
    async def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={})
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client)
        state.decision_timeout = .01
        with pytest.raises(ProviderUnavailable, match="Время разбора"):
            await compile_tasks(state, "Собери малину", world(snapshot))
        assert not state.inference_tasks and not state.task_pool.tasks


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
@pytest.mark.parametrize("confirmed", [False, True])
async def test_whole_bag_scope_is_independently_confirmed_for_every_provider(snapshot, provider, confirmed):
    calls = []
    objective = "Гоша, отдай мне все свои вещи" if confirmed else "Гоша, дай мне пять камней"
    def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={})
        body = json.loads(request.content)
        schema = body["format"] if provider == "ollama" else body["response_format"]["json_schema"]["schema"] if provider == "openai" else body["generationConfig"]["responseJsonSchema"]
        phase = "scope" if "confirmed" in schema.get("properties", {}) else "transfer" if "transfers" in schema.get("properties", {}) else "plan"
        calls.append((phase, body))
        if phase == "scope":
            messages = body.get("messages")
            original = messages[-1]["content"] if messages else body["contents"][0]["parts"][0]["text"]
            assert original == objective
            value = {"confirmed": confirmed}
        elif phase == "transfer":
            original = body["messages"][-1]["content"] if "messages" in body else body["contents"][0]["parts"][0]["text"]
            assert set(json.loads(original)) == {"latest_request", "item_types"}
            value = {"transfers": [{"kind": "give", "item_prefabs": [], "amount": 1, "selection": "all_inventory"}]}
        else:
            value = model_batch(schema)
            value["tasks"][1] = model_task(schema, "give", index=1, selection="all_inventory")
            if provider == "ollama": assert body["options"]["num_predict"] == 6144
        text = json.dumps(value)
        if provider == "ollama":
            assert body["think"] is False
            return httpx.Response(200, json={"done": True, "message": {"content": text}})
        if provider == "openai": return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        state.task_pool.apply_batch([TaskSpec(kind="follow")])
        before = [task.id for task in state.task_pool.tasks]
        if confirmed:
            result = await compile_tasks(state, objective, world(snapshot))
            assert result.tasks[1].selection == "all_inventory"
        else:
            with pytest.raises(ProviderInvalidAction, match="исходное поручение"):
                await compile_tasks(state, objective, world(snapshot))
        assert [task.id for task in state.task_pool.tasks] == before
        assert [phase for phase, _ in calls] == (["plan", "scope", "transfer"] if confirmed else ["plan", "scope"])
        traces = list(state.dialogue.records.values())
        assert [record.purpose for record in traces] == (["tasks", "task-scope", "task-transfer"] if confirmed else ["tasks", "task-scope"])
        assert traces[0].status == ("ok" if confirmed else "error")
        assert traces[1].status == "ok"


async def test_wrapped_atomic_whole_bag_action_is_rejected_before_scope_review(snapshot):
    calls = []
    def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={})
        body = json.loads(request.content)
        scope = "confirmed" in body["format"].get("properties", {})
        calls.append(scope)
        if scope: value = {"confirmed": False}
        else:
            value = {"tasks": [{"kind": "action", "action": {
                "action": "give", "target_id": "leader", "item_id": "", "amount": 1,
                "selection": "all_inventory", "duration": 1, "say": "Передам вещи",
            }}]}
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(value)}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client)
        with pytest.raises(ProviderInvalidAction, match="допустимые задачи"):
            await compile_tasks(state, "Дай мне пять камней", world(snapshot))
        assert calls == [False] and not state.task_pool.tasks


async def test_truthy_string_is_not_a_whole_inventory_confirmation(snapshot):
    def handler(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        if "confirmed" in schema.get("properties", {}): value = {"confirmed": "true"}
        else:
            value = model_batch(schema)
            value["tasks"][1] = model_task(schema, "drop", index=1, selection="all_inventory")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, "openai")
        with pytest.raises(ProviderInvalidAction, match="не подтвердила объём"):
            await compile_tasks(state, "Сбрось все вещи", world(snapshot))
        assert not state.task_pool.tasks


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
@pytest.mark.parametrize("objective,amount,selection,confirmed", [
    ("Следуй за мной и собирай малину", 5, "exact", False),
    ("Дай мне пять камней", 3, "exact", False),
    ("Дай мне пять камней", 50, "exact", False),
    ("Дай мне пять камней", 5, "exact", True),
    ("Сбрось все камни", 1, "all_matching", True),
])
async def test_each_typed_transfer_is_reviewed_against_original_request_before_pool_mutation(snapshot, provider, objective, amount, selection, confirmed):
    calls = []
    kind = "drop" if selection == "all_matching" else "give"
    def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={})
        body = json.loads(request.content)
        schema = body["format"] if provider == "ollama" else body["response_format"]["json_schema"]["schema"] if provider == "openai" else body["generationConfig"]["responseJsonSchema"]
        review = "transfers" in schema.get("properties", {})
        calls.append(review)
        if review:
            source = body["messages"][-1]["content"] if "messages" in body else body["contents"][0]["parts"][0]["text"]
            facts = json.loads(source)
            assert facts["latest_request"] == objective
            assert set(facts) == {"latest_request", "item_types"}  # No proposed count or generated prose.
            assert any(i["item_prefab"] == "Stone" and i["name"] == "Камень" for i in facts["item_types"])
            value = {"transfers": [] if objective.startswith("Следуй") else [{"kind": kind, "item_prefabs": ["Stone"], "amount": amount if confirmed else 5, "selection": selection}]}
        else:
            value = {"tasks": [model_task(schema, kind, item_prefabs=["Stone"], amount=amount, selection=selection)]}
        text = json.dumps(value)
        if provider == "ollama":
            assert body["think"] is False
            return httpx.Response(200, json={"done": True, "message": {"content": text}})
        if provider == "openai": return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        state.task_pool.apply_batch([TaskSpec(kind="follow")])
        before = [task.id for task in state.task_pool.tasks]
        if confirmed:
            batch = await compile_tasks(state, objective, world(snapshot))
            assert batch.tasks[0].amount == amount and batch.tasks[0].selection == selection
        else:
            with pytest.raises(ProviderInvalidAction, match="исходного поручения"):
                await compile_tasks(state, objective, world(snapshot))
        assert [task.id for task in state.task_pool.tasks] == before
        assert calls == [False, True]
        assert [record.purpose for record in state.dialogue.records.values()] == ["tasks", "task-transfer"]


async def test_transfer_review_rejects_yes_instead_of_independent_extraction(snapshot):
    def handler(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        if "transfers" in schema.get("properties", {}): value = {"confirmed": True}
        else:
            value = {"tasks": [model_task(schema, "give", item_prefabs=["Stone"], amount=5)]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderInvalidAction, match="Не удалось независимо определить предметы"):
            await compile_tasks(state_for(client, "openai"), "Дай пять камней", world(snapshot))


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
def test_cancel_only_proposal_removes_requested_job_and_preserves_follow(snapshot, provider):
    observed = world(snapshot)
    pool = TaskPool()
    following, berries = pool.apply_batch([TaskSpec(kind="follow"),
        TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True)], snapshot=observed)
    request = task_request("Больше не собирай малину", observed, existing_tasks=pool.tasks, provider=provider)
    allowed = request["format"]["$defs"]["TaskEdit"]["properties"]["task_id"]["enum"]
    assert set(allowed) == {following.id, berries.id}
    edit = {"action":"cancel", "task_id":berries.id}
    batch = parse_tasks({"tasks":[], "edits":[edit]}, observed, existing_tasks=pool.tasks)
    pool.apply_batch(batch, snapshot=observed)
    assert [task.id for task in pool.tasks] == [following.id]
    assert pool.history[-1].id == berries.id and pool.history[-1].status == "cancelled"
    with pytest.raises(ValueError, match="existing task"):
        parse_tasks({"tasks":[], "edits":[{"action":"cancel", "task_id":"invented"}]},
                    observed, existing_tasks=pool.tasks)


def test_requested_finite_pickup_outranks_ready_standing_gathering(snapshot):
    observed = world(snapshot)
    observed.entities[-1] = observed.entities[-1].model_copy(update={"item_prefab":"Stone"})
    observed.entities.append(observed.entities[-1].model_copy(update={"id":"berries", "name":"Малина", "item_prefab":"Raspberry"}))
    pool = TaskPool()
    standing = pool.apply_batch([TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=60)], snapshot=observed)[0]
    requested = parse_tasks({"tasks":[{"kind":"collect", "item_prefabs":["Stone"], "amount":1, "selection":"exact",
                                      "continuous":False, "near_leader":False, "objective":"", "priority":70}]}, observed, require_priority=True)
    assert requested.tasks[0].priority == 70
    pool.apply_batch(requested, snapshot=observed)
    decision, _, selected = pool.next_decision(observed)
    assert decision.action == "pickup" and decision.target_id == "drop"
    assert selected.spec.item_prefabs == ["Stone"]
    assert standing.id in pool.nodes


def test_model_priorities_and_explicit_dependency_survive_native_id_binding(snapshot):
    observed = world(snapshot)
    batch = parse_tasks({"tasks":[
        {"id":"acquire", "kind":"collect", "item_prefabs":["Stone"], "amount":5, "priority":14},
        {"id":"deliver", "kind":"give", "item_prefabs":["Stone"], "amount":5, "priority":89, "depends_on":["acquire"]},
    ]}, observed, require_priority=True)
    collect, give = batch.tasks
    assert give.depends_on == [collect.id]
    assert (collect.priority, give.priority) == (14, 89)
    pool = TaskPool()
    accepted = pool.apply_batch(batch, snapshot=observed)
    assert accepted[1].spec.depends_on == [accepted[0].id]
    assert accepted[0].id != collect.id


def test_parser_does_not_invent_priority_or_dependencies_for_legacy_native_callers(snapshot):
    batch = parse_tasks({"tasks":[
        {"kind":"collect", "item_prefabs":["Stone"], "amount":5},
        {"kind":"give", "item_prefabs":["Stone"], "amount":5},
    ]}, world(snapshot))
    assert [t.priority for t in batch.tasks] == [50, 50]
    assert all(not t.depends_on for t in batch.tasks)
