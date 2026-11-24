import json

import httpx
import pytest

from valheim_ai.providers import ProviderInvalidAction
from valheim_ai.task_compiler import compile_tasks, parse_tasks, task_request
from valheim_ai.task_pool import TaskBatch, TaskEdit, TaskPool, TaskSpec
from test_task_compiler import state_for, world


def standing_pool():
    pool = TaskPool()
    follow, stone, berries = pool.apply_batch([
        TaskSpec(kind="follow", priority=37),
        TaskSpec(kind="collect", item_prefabs=["Stone"], continuous=True, priority=74),
        TaskSpec(kind="collect", item_prefabs=["Raspberry"], continuous=True, priority=62),
    ])
    return pool, follow, stone, berries


def test_repeated_identical_cancel_is_one_edit_and_removes_only_model_selected_task(snapshot):
    pool, follow, stone, berries = standing_pool()
    raw = {"tasks": [], "edits": [
        {"action": "cancel", "task_id": stone.id},
        {"action": "cancel", "task_id": stone.id},
    ]}
    batch = parse_tasks(raw, world(snapshot), existing_tasks=pool.tasks)
    assert len(batch.edits) == 1
    pool.apply_batch(batch, source_request="Камни больше не нужны")
    assert pool.get(stone.id) is None
    assert [(node.id, node.priority) for node in pool.tasks] == [(follow.id, 37), (berries.id, 62)]
    assert len(pool.history) == 1 and pool.history[0].status == "cancelled"


@pytest.mark.parametrize("edits", [
    [("cancel", 50), ("pause", 50)],
    [("pause", 50), ("resume", 50)],
    [("priority", 12), ("priority", 88)],
])
def test_conflicting_edits_reach_format_repair_boundary_without_choosing_for_ai(snapshot, edits):
    pool, _, stone, _ = standing_pool()
    before = pool.status()
    raw = {"edits": [{"action": action, "task_id": stone.id, **({"priority": priority} if action == "priority" else {})}
                     for action, priority in edits]}
    with pytest.raises(ProviderInvalidAction, match=r"\$\.edits\[1\]\.task_id.*Conflicting"):
        parse_tasks(raw, world(snapshot), existing_tasks=pool.tasks)
    assert pool.status() == before


def test_paused_pool_accepts_model_cancellations_without_resuming_other_jobs(snapshot):
    pool, follow, stone, berries = standing_pool()
    pool.control("pause")
    batch = parse_tasks({"edits": [{"action": "cancel", "task_id": stone.id},
                                   {"action": "cancel", "task_id": berries.id}]},
                        world(snapshot), existing_tasks=pool.tasks)
    pool.apply_batch(batch)
    assert pool.paused and [(node.id, node.priority) for node in pool.tasks] == [(follow.id, 37)]
    assert pool.next_decision(world(snapshot))[0].action.value == "wait"


def test_replace_and_edits_is_repairable_before_commit(snapshot):
    pool, _, stone, _ = standing_pool()
    with pytest.raises(ProviderInvalidAction, match=r"\$\.mode"):
        parse_tasks({"mode": "replace", "edits": [{"action": "cancel", "task_id": stone.id}]},
                    world(snapshot), existing_tasks=pool.tasks)


async def test_conflicting_cancel_is_corrected_by_model_once_then_applied(snapshot):
    calls = []
    pool, follow, stone, berries = standing_pool()
    def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities": ["completion"]})
        body = json.loads(request.content)
        facts = json.loads(body["messages"][1]["content"])
        calls.append(facts)
        assert body["think"] is False
        if len(calls) == 1:
            value = {"edits": [{"action": "cancel", "task_id": stone.id},
                               {"action": "pause", "task_id": stone.id}]}
        else:
            assert facts["latest_request"] == "Прекращай собирать камни"
            assert "$.edits[1].task_id" in facts["format_repair"]["validation_errors"]
            assert "Conflicting" in facts["format_repair"]["validation_errors"]
            value = {"edits": [{"action": "cancel", "task_id": stone.id}]}
        return httpx.Response(200, json={"message": {"content": json.dumps(value)}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client)
        state.task_pool = pool
        batch = await compile_tasks(state, "Прекращай собирать камни", world(snapshot))
        assert len(calls) == 2 and state.task_pool.get(stone.id) is stone
        state.task_pool.apply_batch(batch)
        assert state.task_pool.get(stone.id) is None
        assert [(node.id, node.priority) for node in pool.tasks] == [(follow.id, 37), (berries.id, 62)]
        assert [record.purpose for record in state.dialogue.records.values()] == ["tasks", "tasks-repair"]


def test_preparation_request_contains_actual_equipment_and_food_capabilities(snapshot):
    observed = world(snapshot)
    observed.actor.weapon = "Дубина"
    observed.actor.inventory[1] = observed.actor.inventory[1].model_copy(update={
        "category": "weapon", "armor": 3, "damage": 18, "block_power": 4,
        "quality": 2, "usable": True})
    observed.actor.inventory[0] = observed.actor.inventory[0].model_copy(update={
        "food_health": 12, "food_stamina": 20, "can_eat": False})
    facts = json.loads(task_request("Подготовься к бою", observed)["messages"][1]["content"])
    weapon = facts["npc"]["inventory"][1]
    assert {key: weapon[key] for key in ("equipable", "category", "armor", "damage", "block_power", "quality", "usable")} == {
        "equipable": True, "category": "weapon", "armor": 3, "damage": 18, "block_power": 4, "quality": 2, "usable": True}
    food = facts["npc"]["inventory"][0]
    assert food["food_health"] == 12 and food["food_stamina"] == 20 and food["can_eat"] is False
    assert facts["npc"]["weapon"] == "Дубина"


def test_task_edits_are_first_class_in_decoder_and_use_observed_ids(snapshot):
    pool, follow, stone, berries = standing_pool()
    request = task_request("Прекращай собирать", world(snapshot), existing_tasks=pool.tasks)
    assert next(iter(request["format"]["properties"])) == "edits"
    assert all(branch["properties"]["task_id"]["enum"] == [follow.id, stone.id, berries.id]
               for branch in request["format"]["$defs"]["TaskEdit"]["anyOf"])
