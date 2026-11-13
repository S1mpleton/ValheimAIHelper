import pytest

from valheim_ai.protocol import Decision, Snapshot, validate_decision
from valheim_ai.local_schema import action_schema
from valheim_ai.task_policy import planning_view


def objects(snapshot):
    snapshot["actor"]["inventory"]=[]
    snapshot["supported_actions"]=["wait","pickup","interact","gather","speak"]
    snapshot["entities"] += [
        {"id":"plant","name":"Гриб","kind":"interactable","category":"pickable","interaction":"interact","distance":2,"position":{"x":2,"y":0,"z":0}},
        {"id":"ore","name":"Залежь","kind":"resource","category":"MineRock5","interaction":"gather","required_tool":"pickaxe","tool_tier":1,"distance":2,"position":{"x":2,"y":0,"z":0}},
    ]
    return Snapshot.model_validate(snapshot)


def test_empty_bag_dropped_stone_and_mushroom_do_not_require_tools_and_mining_is_not_offered(snapshot):
    world=objects(snapshot)
    schema=action_schema(world,True)
    actions={v["properties"]["action"]["const"]:v["properties"] for v in schema["$defs"]["Decision"]["anyOf"]}
    assert "drop" in actions["pickup"]["target_id"]["enum"]
    assert "plant" in actions["interact"]["target_id"]["enum"]
    assert "gather" not in actions
    assert schema["properties"]["steps"]["maxItems"]==1
    assert validate_decision(Decision(action="pickup",target_id="drop",duration=1,say="Подберу без кирки"),world)
    assert validate_decision(Decision(action="interact",target_id="plant",duration=1,say="Сорву гриб"),world)
    with pytest.raises(ValueError,match="tool"):
        validate_decision(Decision(action="gather",target_id="ore",duration=1,say="Добуду"),world)
    assert "gather" not in planning_view("Подбери камень",world,()).supported_actions


def test_only_existing_adequate_unbroken_tool_enables_mining(snapshot):
    world=objects(snapshot)
    from valheim_ai.protocol import InventoryItem
    tool=InventoryItem(id="pickaxe",name="Кирка",count=1,equipped=False,food=False,equipable=True,skill="Pickaxes",tool_tier=1,durability=1)
    world.actor.inventory=[tool]
    def targets():
        return {v["properties"]["action"]["const"]:v["properties"]["target_id"] for v in action_schema(world,True)["$defs"]["Decision"]["anyOf"]}
    assert targets()["gather"]["enum"]==["ore"]
    assert validate_decision(Decision(action="gather",target_id="ore",duration=1,say="Добуду"),world)
    for change in ({"tool_tier":0},{"durability":0},{"skill":"Axes"}):
        world.actor.inventory=[tool.model_copy(update=change)]
        assert "gather" not in targets()
