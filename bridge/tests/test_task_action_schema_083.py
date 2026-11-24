from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from valheim_ai.protocol import Action, Decision, Snapshot, validate_decision
from valheim_ai.task_action_schema import ACTION_DURATIONS, TYPED_ACTIONS, task_action_schema


def action(name, target="", **extra):
    return {"action": name, "target_id": target, "duration": 1, "say": "", "amount": 1, **extra}


def observed(snapshot, actions):
    snapshot["supported_actions"] = actions
    return Snapshot.model_validate(snapshot)


def validate(schema, value):
    assert Draft202012Validator(schema).is_valid(value)


def test_actual_prepare_for_battle_reply_cannot_attack_the_human(snapshot):
    world = observed(snapshot, ["attack", "equip", "wait"])
    schema = task_action_schema(world)
    validator = Draft202012Validator(schema)
    # The user's actual model response attacked character:-894096 (leader)
    # instead of preparing equipment. A repair repeated the same forbidden ID.
    assert not validator.is_valid(action("attack", "leader"))
    validate(schema, action("attack", "enemy"))
    validate(schema, action("equip", "inventory:1"))
    assert not validator.is_valid(action("equip", "inventory:0"))
    assert not validator.is_valid(action("equip", "invented-armor"))
    assert validate_decision(Decision.model_validate(action("equip", "inventory:1")), world).action == Action.EQUIP


def test_no_hostiles_removes_combat_targets_but_keeps_equipment_and_wait(snapshot):
    snapshot["entities"] = [entity for entity in snapshot["entities"] if entity["kind"] != "enemy"]
    schema = task_action_schema(observed(snapshot, ["attack", "secondary_attack", "defend", "flee", "dodge", "equip"]))
    assert schema["$defs"]["Action"]["enum"] == ["wait", "equip"]
    validate(schema, action("wait"))
    validate(schema, action("equip", "inventory:1"))


def test_equipped_armor_is_an_observed_equip_target(snapshot):
    snapshot["actor"]["inventory"].append(dict(id="rags", name="Холщовая туника", count=1,
        equipped=False, food=False, equipable=True, item_prefab="ArmorRagsChest", armor=1))
    world = observed(snapshot, ["equip", "unequip"])
    schema = task_action_schema(world)
    for name in ("equip", "unequip"):
        value = action(name, "rags")
        validate(schema, value)
        assert validate_decision(Decision.model_validate(value), world).target_id == "rags"


def test_food_and_usable_items_have_distinct_observed_ids(snapshot):
    snapshot["actor"]["inventory"][0]["can_eat"] = False
    snapshot["actor"]["inventory"].append(dict(id="potion", name="Медовуха", count=2,
        equipped=False, food=False, equipable=False, usable=True))
    schema = task_action_schema(observed(snapshot, ["eat", "use"]))
    assert schema["$defs"]["Action"]["enum"] == ["wait", "use"]
    validate(schema, action("use", "potion"))
    assert not Draft202012Validator(schema).is_valid(action("use", "inventory:1"))


@pytest.mark.parametrize("distance,available", [(45, True), (128, True), (129, False)])
def test_native_distant_pickable_interaction_is_preserved(snapshot, distance, available):
    snapshot["entities"].append(dict(id="pickable", name="Гриб", kind="interactable", category="pickable",
        produces=["Mushroom"], position=dict(x=distance, y=0, z=0), distance=distance))
    world = observed(snapshot, ["interact"])
    value = action("interact", "pickable")
    assert Draft202012Validator(task_action_schema(world)).is_valid(value) is available
    if available:
        assert validate_decision(Decision.model_validate(value), world).target_id == "pickable"


def test_actual_distance_is_checked_as_well_as_reported_distance(snapshot):
    snapshot["entities"][1]["position"]["x"] = 21
    schema = task_action_schema(observed(snapshot, ["attack"]))
    assert not Draft202012Validator(schema).is_valid(action("attack", "enemy"))
    assert schema["$defs"]["Action"]["enum"] == ["wait"]


def test_all_duration_choices_are_native_legal_and_ten_is_impossible(snapshot):
    world = observed(snapshot, ["equip"])
    schema = task_action_schema(world)
    for duration in ACTION_DURATIONS:
        value = action("equip", "inventory:1", duration=duration)
        validate(schema, value)
        assert validate_decision(Decision.model_validate(value), world).duration == duration
    assert not Draft202012Validator(schema).is_valid(action("equip", "inventory:1", duration=10))


def rich_world(snapshot):
    snapshot["actor"]["inventory"][1].update(skill="Pickaxes", tool_tier=2, usable=True)
    for name, kind, category in [("animal", "animal", ""), ("ore", "resource", "MineRock"),
            ("chest", "interactable", "container"), ("station", "interactable", "station"),
            ("wall", "interactable", "structure"), ("bed", "interactable", "bed")]:
        snapshot["entities"].append(dict(id=name, name=name, kind=kind, category=category,
            position=dict(x=2, y=0, z=0), distance=2))
    snapshot["container_id"] = "chest"
    snapshot["container_inventory"] = [dict(id="chest:stone", name="Камень", count=3, equipped=False, food=False, equipable=False)]
    snapshot["recipes"] = [dict(id="recipe", name="Дубина", craftable=True)]
    snapshot["build_options"] = [dict(id="building", name="Стена", available=True)]
    return observed(snapshot, [value.value for value in Action])


def test_all_observed_atomic_capabilities_survive_with_consistent_definitions(snapshot):
    world = rich_world(snapshot)
    original = world.model_dump()
    schema = task_action_schema(world)
    Draft202012Validator.check_schema(schema)
    actions = set(schema["$defs"]["Action"]["enum"])
    assert actions == {value.value for value in Action} - TYPED_ACTIONS
    assert not actions.intersection(TYPED_ACTIONS)
    # MOVE/JUMP/LOOK/BUILD keep resolvable native coordinate references.
    point = dict(x=1, y=0, z=0)
    for value in (action("move", destination=point), action("jump", destination=point),
            action("look", destination=point), action("build", "building", destination=point)):
        validate(schema, value)
        validate_decision(Decision.model_validate(value), world)
    assert world.model_dump() == original


def test_emotes_and_craft_batch_counts_use_native_choices(snapshot):
    world = rich_world(snapshot)
    schema = task_action_schema(world)
    validate(schema, action("emote", text="wave"))
    assert not Draft202012Validator(schema).is_valid(action("emote", text="unimplemented_dance"))
    for amount in (1, 10):
        validate(schema, action("craft", "recipe", amount=amount))
    assert not Draft202012Validator(schema).is_valid(action("craft", "recipe", amount=11))


def test_schema_can_be_embedded_in_task_batch_without_dangling_refs(snapshot):
    atomic = task_action_schema(rich_world(snapshot))
    definitions = atomic.pop("$defs")
    definitions["Decision"] = atomic
    root = {"type": "object", "properties": {"action": {"$ref": "#/$defs/Decision"}}, "$defs": definitions}
    validate(root, {"action": action("look", destination=dict(x=1, y=0, z=0))})


def test_unadvertised_and_unknown_actions_do_not_become_capabilities(snapshot):
    schema = task_action_schema(observed(snapshot, ["equip", "execute_code", *TYPED_ACTIONS]))
    assert schema["$defs"]["Action"]["enum"] == ["wait", "equip"]


@pytest.mark.parametrize("container_exists", [False, True])
def test_stale_open_container_id_cannot_offer_unobserved_or_wrong_kind_transfers(snapshot, container_exists):
    snapshot["container_id"] = "not-a-chest"
    snapshot["container_inventory"] = [dict(id="old-item", name="Камень", count=1,
        equipped=False, food=False, equipable=False)]
    if container_exists:
        snapshot["entities"].append(dict(id="not-a-chest", name="Станция", kind="interactable",
            category="station", position=dict(x=2, y=0, z=0), distance=2))
    schema = task_action_schema(observed(snapshot, ["take", "deposit"]))
    assert schema["$defs"]["Action"]["enum"] == ["wait"]
