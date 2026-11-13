import json

import pytest

from valheim_ai.item_intent import (
    ItemIntent, blocked_reply, intent_request, parse_intent, scope_view, validate_item_decision,
)
from valheim_ai.protocol import Decision, Entity, InventoryItem, Snapshot


def catalog_item(prefab, name, token="", category="Material"):
    return {"item_prefab": prefab, "name": name, "token": token, "category": category}


def observed(prefab, name, *, kind="item", category="", produces=(), count=1, object_id=None):
    # model_copy allows the fixture to remain compatible during the protocol's
    # addition of stable prefab identities; production uses validated fields.
    return Entity(id=object_id or "world:" + prefab, name=name, kind=kind,
                  position={"x": 1, "y": 0, "z": 0}, distance=1, category=category, count=count).model_copy(
                      update={"item_prefab": prefab if kind == "item" else "", "produces": list(produces)})


def bag(prefab, name, count=1, *, item_id=None, quest=False):
    return InventoryItem(id=item_id or "bag:" + prefab, name=name, count=count, equipped=False, food=False,
                         equipable=False, quest=quest, item_key=prefab + ":1:0").model_copy(update={"item_prefab": prefab})


def world(snapshot, *, entities=(), inventory=(), catalog=()):
    state = Snapshot.model_validate(snapshot)
    players = [e for e in state.entities if e.kind == "player"]
    return state.model_copy(update={
        "entities": [*players, *entities], "actor": state.actor.model_copy(update={"inventory": list(inventory)}),
        "item_catalog": list(catalog), "supported_actions": ["wait", "speak", "follow", "pickup", "interact", "give", "drop", "eat", "equip"],
    })


def step(action, target="", **extra):
    return Decision(action=action, target_id=target, duration=1, say="Выполняю", **extra)


def test_missing_catalog_axe_does_not_turn_into_fire_interaction(snapshot):
    fire = observed("Fire", "Костёр", kind="interactable", category="fire")
    state = world(snapshot, entities=[fire], catalog=[catalog_item("AxeStone", "Каменный топор", "$item_axe_stone")])
    intent = parse_intent({"goal": "collect", "item_prefabs": ["AxeStone"]}, state)
    scoped = scope_view(intent, state)
    assert scoped.supported_actions == ["wait", "speak"]
    assert fire not in scoped.entities
    assert "Каменный топор" in blocked_reply(intent, state)
    with pytest.raises(ValueError, match="does not match"):
        validate_item_decision(intent, step("interact", fire.id), state)
    assert validate_item_decision(intent, Decision(
        action="wait", target_id="", duration=1, say="Топор не вижу"), state).action.value == "wait"


def test_visible_drop_waiting_for_native_ownership_remains_a_pickup_target(snapshot):
    drop = observed("Stone", "Камень", object_id="stone:far").model_copy(update={"pickup_allowed": False, "distance": 60})
    state = world(snapshot, entities=[drop])
    intent = ItemIntent(goal="collect", item_prefabs=["Stone"])
    assert "pickup" in scope_view(intent, state).supported_actions
    assert not blocked_reply(intent, state)
    validate_item_decision(intent, step("pickup", drop.id), state)


def test_multistage_token_speed_is_weighted_by_total_generation_time():
    from valheim_ai.performance import accumulate_statistics
    record = {"provider": "ollama"}
    accumulate_statistics(record, {"generation_seconds": 1, "output_tokens": 300, "prompt_tokens": 600, "load_seconds": 2})
    accumulate_statistics(record, {"generation_seconds": 3, "output_tokens": 100, "prompt_tokens": 200, "load_seconds": 0})
    assert record["generation_seconds"] == 4 and record["output_tokens"] == 400
    assert record["tokens_per_second"] == 100 and record["prompt_tokens"] == 800 and record["load_seconds"] == 2


def test_collect_matches_stable_plant_output_and_ground_prefab_not_other_plant(snapshot):
    berry = observed("Bush", "Малиновый куст", kind="interactable", category="pickable", produces=["Raspberry"])
    mushroom = observed("Mushroom", "Гриб", kind="interactable", category="pickable", produces=["Mushroom"])
    drop = observed("Raspberry", "Малина", object_id="drop:berries")
    state = world(snapshot, entities=[berry, mushroom, drop], catalog=[catalog_item("Raspberry", "Малина")])
    intent = ItemIntent(goal="collect", item_prefabs=["Raspberry"])
    scoped = scope_view(intent, state)
    assert {e.id for e in scoped.entities if e.kind != "player"} == {berry.id, drop.id}
    assert {"pickup", "interact"} <= set(scoped.supported_actions)
    assert not blocked_reply(intent, state)
    validate_item_decision(intent, step("interact", berry.id), state)
    validate_item_decision(intent, step("pickup", drop.id), state)
    with pytest.raises(ValueError, match="does not produce"):
        validate_item_decision(intent, step("interact", mushroom.id), state)


def test_give_resolved_stones_never_selects_dandelion_or_wrong_quantity(snapshot):
    stone, flower = bag("Stone", "Камень", 7), bag("Dandelion", "Одуванчик", 50)
    state = world(snapshot, inventory=[stone, flower])
    intent = ItemIntent(goal="give", item_prefabs=["Stone"], amount=5)
    scoped = scope_view(intent, state)
    assert scoped.actor.inventory == state.actor.inventory  # survival data stays intact
    assert "give" in scoped.supported_actions and "pickup" not in scoped.supported_actions
    assert not blocked_reply(intent, state)
    validate_item_decision(intent, step("give", "leader", item_id=stone.id, amount=5), state)
    with pytest.raises(ValueError, match="does not match"):
        validate_item_decision(intent, step("give", "leader", item_id=flower.id, amount=5), state)
    with pytest.raises(ValueError, match="quantity"):
        validate_item_decision(intent, step("give", "leader", item_id=stone.id, amount=7), state)


def test_insufficient_give_can_collect_matching_missing_items_first(snapshot):
    state = world(snapshot, inventory=[bag("Stone", "Камень", 2)],
                  entities=[observed("Stone", "Камень", count=7), observed("Wood", "Древесина")])
    intent = ItemIntent(goal="give", item_prefabs=["Stone"], amount=5)
    assert "give" not in scope_view(intent, state).supported_actions
    assert "pickup" in scope_view(intent, state).supported_actions
    assert not blocked_reply(intent, state)
    validate_item_decision(intent, step("pickup", "world:Stone"), state)
    with pytest.raises(ValueError):
        validate_item_decision(intent, step("pickup", "world:Wood"), state)


def test_exact_quantity_spans_matching_stacks_but_not_different_native_variants(snapshot):
    a = bag("Stone", "Камень", 5, item_id="stone:1")
    b = bag("Stone", "Камень", 3, item_id="stone:2")
    intent = ItemIntent(goal="give", item_prefabs=["Stone"], amount=7)
    state = world(snapshot, inventory=[a, b])
    validate_item_decision(intent, step("give", "leader", item_id=a.id, amount=7), state)
    different = b.model_copy(update={"item_key": "Stone:2:0", "quality": 2})
    wrong = world(snapshot, inventory=[a, different])
    assert "give" not in scope_view(intent, wrong).supported_actions
    assert blocked_reply(intent, wrong)


def test_whole_bag_transfer_and_quest_only_bag(snapshot):
    state = world(snapshot, inventory=[bag("Stone", "Камень", 3), bag("Dandelion", "Одуванчик")])
    intent = ItemIntent(goal="give", selection="all_inventory")
    validate_item_decision(intent, step("give", "leader", item_id="", amount=1, selection="all_inventory"), state)
    assert not blocked_reply(intent, state)
    with pytest.raises(ValueError, match="single item"):
        validate_item_decision(intent, step("give", "leader", item_id="bag:Stone", amount=1, selection="all_inventory"), state)
    quest = world(snapshot, inventory=[bag("CryptKey", "Ключ", quest=True)])
    assert blocked_reply(intent, quest)
    assert "give" not in scope_view(intent, quest).supported_actions


def test_general_task_is_not_reinterpreted_and_question_cannot_touch_fire(snapshot):
    state = world(snapshot, entities=[observed("Fire", "Костёр", kind="interactable", category="fire")])
    assert scope_view(ItemIntent(goal="general"), state) is state
    validate_item_decision(ItemIntent(goal="general"), step("follow", "leader"), state)
    reply = ItemIntent(goal="reply")
    assert scope_view(reply, state).supported_actions == ["wait", "speak"]
    with pytest.raises(ValueError, match="only receive a reply"):
        validate_item_decision(reply, step("interact", "world:Fire"), state)


def test_candidate_retrieval_uses_names_and_identity_keeps_all_observed_types(snapshot):
    catalog = [catalog_item("Junk" + str(i), "Другой предмет " + str(i)) for i in range(100)]
    catalog += [catalog_item("AxeStone", "Каменный топор", "$item_axe_stone"), catalog_item("Stone", "Камень")]
    state = world(snapshot, entities=[observed("Wood", "Древесина")], inventory=[bag("Club", "Дубина")], catalog=catalog)
    request = intent_request(state, "Гоша, подбери топоры", chat=[{"text": "Обсудим погоду"}], history=["pickup: Камень ×7"])
    prefabs = [i["item_prefab"] for i in request["candidates"]]
    assert prefabs[0] == "AxeStone" and {"Wood", "Club"} <= set(prefabs)
    assert len(prefabs) <= 50  # top48 plus the two observed types
    payload = json.loads(request["messages"][-1]["content"])
    assert payload["latest_request"] == "Гоша, подбери топоры"
    assert {i["source"] for i in payload["observed_stock"]} == {"ground", "bag"}
    assert payload["recent_chat"] and payload["execution_history"]
    assert "AxeStone" not in {i["item_prefab"] for i in payload["observed_stock"]}
    parse_intent({"goal": "collect", "item_prefabs": ["AxeStone"]}, state, request["candidates"])
    with pytest.raises(ValueError, match="not in the game catalog"):
        parse_intent({"goal": "collect", "item_prefabs": ["SpawnedAxe"]}, state, request["candidates"])


def test_item_goal_cannot_silently_become_empty_or_ambiguous_transfer():
    for value in [
        {"goal": "collect", "item_prefabs": []},
        {"goal": "give", "item_prefabs": ["Stone"], "selection": "all_inventory"},
        {"goal": "general", "item_prefabs": ["Stone"]},
        {"goal": "collect", "item_prefabs": ["Stone", "Stone"]},
    ]:
        with pytest.raises(ValueError):
            ItemIntent.model_validate(value)


def test_pronoun_retrieval_uses_recent_context_without_overriding_explicit_new_item(snapshot):
    state = world(snapshot, catalog=[catalog_item("Stone", "Камень"), catalog_item("AxeStone", "Каменный топор")])
    chat = [{"speaker": "Лидер", "text": "Видишь топор?"}]
    assert intent_request(state, "Подбери его", chat=chat, limit=1)["candidates"][0]["item_prefab"] == "AxeStone"
    assert intent_request(state, "Подбери камень", chat=chat, limit=1)["candidates"][0]["item_prefab"] == "Stone"


def test_empty_catalog_has_valid_reply_and_bulk_intents_without_empty_enum(snapshot):
    state = world(snapshot)
    request = intent_request(state, "Неизвестная вещь")
    assert request["candidates"] == []
    assert '"enum": []' not in json.dumps(request["format"])
    assert all(variant["required"] == list(variant["properties"]) for variant in request["format"]["anyOf"])


def test_give_with_real_stock_but_without_visible_recipient_is_blocked(snapshot):
    state = world(snapshot, inventory=[bag("Stone", "Камень", 5)]).model_copy(update={"entities": [], "leader_id": ""})
    intent = ItemIntent(goal="give", item_prefabs=["Stone"], amount=5)
    assert "give" not in scope_view(intent, state).supported_actions
    assert "игрока-получателя" in blocked_reply(intent, state)
