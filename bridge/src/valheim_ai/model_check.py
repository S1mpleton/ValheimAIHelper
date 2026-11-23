"""Probe the production task compiler without exposing or controlling a game."""
from types import SimpleNamespace

from .protocol import Action, Snapshot
from .providers import DECISION_TIMEOUT, OLLAMA_DECISION_TIMEOUT, ProviderInvalidAction
from .task_compiler import compile_tasks
from .task_pool import TaskPool


def check_snapshot():
    return Snapshot(
        session_id="api-check", sequence=1, task_pool_supported=True,
        world_id="api-check-world", supported_actions=["wait", "follow", "pickup", "interact"],
        leader_id="api-check-leader", leader_name="Проверочный игрок", leader_status="visible",
        actor={"id": "api-check-npc", "name": "Проверочный NPC", "position": {"x": 0, "y": 0, "z": 0},
               "health": 25, "max_health": 25, "stamina": 50, "free_slots": 16},
        entities=[
            {"id": "api-check-leader", "name": "Проверочный игрок", "kind": "player", "distance": 5,
             "position": {"x": 5, "y": 0, "z": 0}},
            {"id": "api-check-stone", "name": "Камень", "kind": "item", "distance": 2,
             "position": {"x": 2, "y": 0, "z": 0}, "item_prefab": "Stone", "count": 1},
        ],
        ground_items_total=1,
        item_catalog=[{"item_prefab": "Stone", "name": "Камень", "token": "$item_stone", "category": "material"}],
    )


async def check_task_planner(state, provider, model, key, base):
    # Reuse adapters, cancellation registration and diagnostics, but give the
    # compiler a separate pool and synthetic facts, never the connected NPC.
    pool = TaskPool()
    probe = check_snapshot()
    isolated = SimpleNamespace(
        provider=provider, model=model, keys={provider: key}, ollama_url=base,
        client_for=state.client_for, ollama_session=state.ollama_session,
        inference_tasks=state.inference_tasks, performance=state.performance,
        dialogue=state.dialogue, task_pool=pool,
        decision_timeout=OLLAMA_DECISION_TIMEOUT if provider == "ollama" else DECISION_TIMEOUT,
    )
    proposal = await compile_tasks(isolated,
        "Следуй за мной и постоянно собирай камни по пути рядом со мной. Это проверка на вымышленном мире.", probe)
    kinds = [task.kind for task in proposal.tasks]
    collect = next((task for task in proposal.tasks if task.kind == "collect"), None)
    if (sorted(kinds) != ["collect", "follow"] or proposal.edits
            or collect.item_prefabs != ["Stone"] or not collect.continuous or not collect.near_leader):
        raise ProviderInvalidAction(
            "Формат ответа принят, но модель не прошла проверочное поручение: нужны следование и постоянный сбор Stone рядом с лидером. "
            "Игровой пул не изменён.")
    try:
        pool.apply_batch(proposal, snapshot=probe, source_request="Проверочное поручение")
        decision, _, _ = pool.next_decision(probe)
    except ValueError as exc:
        raise ProviderInvalidAction("Проверочный пул отклонил план: " + str(exc)) from None
    if decision is None or decision.action not in (Action.FOLLOW, Action.PICKUP):
        raise ProviderInvalidAction("Проверочный план не содержит готовой к выполнению задачи. Игровой пул не изменён.")
    # Check both jobs under their actual readiness conditions. A higher follow
    # priority must resume movement when needed, then release the motor after
    # reaching the requested distance; priorities themselves stay untouched.
    for sequence, distance, expected in ((2, 20, Action.FOLLOW), (3, 2, Action.PICKUP)):
        observed = probe.model_copy(update={"sequence": sequence, "entities": [
            entity.model_copy(update={"distance": distance, "position": entity.position.model_copy(update={"x": distance})})
            if entity.id == probe.leader_id else entity for entity in probe.entities
        ]})
        result, _, _ = pool.next_decision(observed)
        if result is None or result.action != expected:
            raise ProviderInvalidAction("Проверочный план не переключается между следованием и сбором при изменении дистанции. Игровой пул не изменён.")
    return ["Формат пула задач: OK", "Следование и сбор: OK"]
