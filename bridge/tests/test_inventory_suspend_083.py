"""The native inventory temporarily gates motor input, not the AI session.

Exercise the actual bridge with the pause flag emitted while that bag is open.
The native side retains its plan identity and receipt during executor.Pause().
"""
import pytest

from valheim_ai.task_pool import TaskSpec
from test_task_runtime import task_client, task_world, connect, poll


async def activate(state, client, snapshot, tasks):
    await connect(client, snapshot)
    nodes = state.tasks.pool.apply_batch(tasks)
    assert (await client.post("/api/command", json={"mode": "ai"})).status_code == 200
    return nodes


async def test_inventory_close_resumes_same_session_and_lease_without_reconfiguring_model(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    follow, = await activate(state, client, observed, [TaskSpec(kind="follow", priority=81)])
    moving = await poll(client, observed)
    revision, pool_revision = state.revision, state.tasks.pool.revision
    provider = state.provider, state.model
    suspended = await poll(client, observed, simulation_paused=True)
    assert suspended["action"] == "follow" and suspended["plan_id"] == moving["plan_id"]
    assert state.command.mode == "ai" and not state.tasks.pool.paused
    resumed = await poll(client, observed, simulation_paused=False)
    assert resumed["action"] == "follow" and resumed["plan_id"] == moving["plan_id"]
    assert state.revision == revision and state.tasks.pool.revision == pool_revision
    assert (state.provider, state.model) == provider and follow.priority == 81
    assert state.tasks.task is None


async def test_f9_during_inventory_is_not_undone_by_closing_it(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    await activate(state, client, observed, [TaskSpec(kind="follow", priority=81)])
    await poll(client, observed)
    await poll(client, observed, simulation_paused=True)
    assert (await client.post("/api/command", json={"mode": "stop"})).status_code == 200
    assert (await poll(client, observed, simulation_paused=False))["action"] == "wait"
    assert state.command.mode == "stop" and not state.tasks.pool.tasks
    assert state.tasks.pool.history[-1].status == "cancelled"


@pytest.mark.parametrize("whole_pool", [True, False])
async def test_inventory_close_preserves_a_manual_pause(task_client, snapshot, whole_pool):
    state, client = task_client
    observed = task_world(snapshot)
    follow, = await activate(state, client, observed, [TaskSpec(kind="follow", priority=81)])
    state.tasks.control("pause", "" if whole_pool else follow.id)
    assert (await poll(client, observed, simulation_paused=True))["action"] == "wait"
    assert (await poll(client, observed, simulation_paused=False))["action"] == "wait"
    assert state.tasks.pool.paused if whole_pool else follow.manual_paused


async def test_committed_give_receipt_is_consumed_once_even_while_inventory_is_open(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    observed["actor"]["inventory"] = [dict(id="bag:stone", item_prefab="Stone", name="Камень", count=4,
                                          equipped=False, equipable=False, food=False)]
    giving, following = await activate(state, client, observed, [
        TaskSpec(kind="give", item_prefabs=["Stone"], amount=2, priority=85),
        TaskSpec(kind="follow", priority=30),
    ])
    issued = await poll(client, observed)
    assert issued["action"] == "give" and issued["amount"] == 2
    observed["actor"]["inventory"][0]["count"] = 2
    receipt = dict(plan_id=issued["plan_id"], action_status="success", action_completed_amount=2)
    assert (await poll(client, observed, simulation_paused=True, **receipt))["action"] == "wait"
    assert giving.status == "completed" and giving.completed_amount == 2
    resumed = await poll(client, observed, simulation_paused=False, **receipt)
    assert resumed["action"] == "follow" and resumed["plan_id"] != issued["plan_id"]
    assert giving.completed_amount == 2 and sum(n.id == giving.id for n in state.tasks.pool.history) == 1
    assert state.tasks.pool.get(following.id) is following


async def test_provider_change_during_inventory_is_not_undone_by_closing_it(task_client, snapshot):
    state, client = task_client
    observed = task_world(snapshot)
    follow, = await activate(state, client, observed, [TaskSpec(kind="follow", priority=81)])
    await poll(client, observed)
    await poll(client, observed, simulation_paused=True)
    response = await client.post("/api/provider", json={"provider": "openai", "model": "different-model"})
    assert response.status_code == 200
    assert (await poll(client, observed, simulation_paused=False))["action"] == "wait"
    assert state.command.mode == "stop" and state.tasks.pool.paused
    assert state.tasks.pool.get(follow.id) is follow and follow.priority == 81


@pytest.mark.parametrize("human_took_stones", [False, True])
async def test_pending_give_keeps_identity_and_revalidates_human_inventory_changes(task_client, snapshot, human_took_stones):
    state, client = task_client
    observed = task_world(snapshot)
    observed["actor"]["inventory"] = [dict(id="bag:stone", item_prefab="Stone", name="Камень", count=4,
                                          equipped=False, equipable=False, food=False)]
    giving, = await activate(state, client, observed, [TaskSpec(kind="give", item_prefabs=["Stone"], amount=2, priority=85)])
    issued = await poll(client, observed)
    assert issued["action"] == "give"
    held = await poll(client, observed, simulation_paused=True)
    assert held["plan_id"] == issued["plan_id"] and held["action"] == "give"
    if human_took_stones:
        observed["actor"]["inventory"] = []
    resumed = await poll(client, observed, simulation_paused=False)
    if human_took_stones:
        assert resumed["action"] == "wait" and giving.status == "failed"
    else:
        assert resumed["action"] == "give" and resumed["plan_id"] == issued["plan_id"]
        assert resumed["amount"] == 2 and giving.completed_amount == 0
