import copy
import pytest

from valheim_ai.protocol import Action, Decision, Snapshot, validate_decision


@pytest.fixture
def world(snapshot):
    snapshot["supported_actions"] = [a.value for a in Action]
    snapshot["actor"]["inventory"].append({"id":"stones", "name":"Камень", "count":20, "equipped":False, "food":False, "equipable":False})
    for ident, kind, category in [("chest","interactable","container"), ("bench","interactable","station"),
                                  ("bed","interactable","bed"), ("wall","interactable","structure"),
                                  ("ore","resource","MineRock5"), ("deer","animal","deer"), ("fuel","interactable","station_input")]:
        snapshot["entities"].append({"id":ident,"name":ident,"kind":kind,"category":category,"distance":2,"position":{"x":2,"y":0,"z":0}})
    snapshot["container_id"] = "chest"
    snapshot["container_inventory"] = [{"id":"stored", "name":"Дерево", "count":10, "equipped":False, "food":False, "equipable":False}]
    snapshot["recipes"] = [{"id":"recipe-axe","name":"Топор","craftable":True,"station":"Верстак"}]
    snapshot["build_options"] = [{"id":"campfire","name":"Костёр","available":True}]
    return snapshot


@pytest.mark.parametrize("action,target,args", [
    ("drop","stones",{"amount":5}), ("unequip","inventory:1",{}),
    ("gather","ore",{}), ("hunt","deer",{}), ("secondary_attack","enemy",{}),
    ("open","chest",{}), ("take","chest",{"item_id":"stored","amount":4}),
    ("deposit","chest",{"item_id":"stones","amount":3}), ("stack","chest",{}),
    ("use_on","fuel",{"item_id":"stones","amount":1}), ("repair","bench",{}),
    ("repair_building","wall",{}), ("sleep","bed",{}),
    ("craft","recipe-axe",{}), ("upgrade","recipe-axe",{"item_id":"inventory:1"}),
    ("build","campfire",{"destination":{"x":4,"y":0,"z":0},"rotation":3}),
    ("hide_weapon","",{}), ("show_weapon","",{}), ("rest","",{}), ("stand","",{}),
    ("guardian_power","",{}), ("sneak","",{}), ("unsneak","",{}),
    ("emote","",{"text":"wave"}), ("speak","",{"text":"Камни собраны"}),
    ("look","",{"destination":{"x":4,"y":1,"z":0}}), ("close","",{}),
])
def test_extended_actions_accept_only_observed_typed_targets(world, action, target, args):
    d = Decision(action=action,target_id=target,duration=1,say="",**args)
    assert validate_decision(d, Snapshot.model_validate(world)).action.value == action


@pytest.mark.parametrize("action,target,args", [
    ("drop","stones",{"amount":21}), ("drop","stored",{"amount":1}),
    ("take","chest",{"item_id":"stones"}), ("deposit","chest",{"item_id":"stored"}),
    ("attack","deer",{}), ("hunt","leader",{}), ("gather","wall",{}),
    ("use_on","fuel",{"item_id":"invented"}), ("craft","invented",{}),
    ("build","campfire",{"destination":{"x":9,"y":0,"z":0}}),
    ("look","",{"destination":{"x":1000,"y":0,"z":0}}), ("emote","",{"text":"execute code"}),
])
def test_extended_action_rejects_wrong_inventory_quantity_or_target(world, action, target, args):
    with pytest.raises(ValueError):
        validate_decision(Decision(action=action,target_id=target,duration=1,say="",**args), Snapshot.model_validate(world))


def test_container_must_be_open_before_transfer(world):
    world["container_id"] = ""
    with pytest.raises(ValueError):
        validate_decision(Decision(action="take",target_id="chest",item_id="stored",duration=1,say=""), Snapshot.model_validate(world))


def test_cannot_craft_or_build_unavailable_options(world):
    world["recipes"][0]["craftable"] = False
    world["build_options"][0]["available"] = False
    for d in [Decision(action="craft",target_id="recipe-axe",duration=1,say=""),
              Decision(action="build",target_id="campfire",destination={"x":2,"y":0,"z":0},duration=1,say="")]:
        with pytest.raises(ValueError): validate_decision(d, Snapshot.model_validate(world))
