import pytest
from pydantic import ValidationError

from valheim_ai.protocol import Decision, Snapshot, validate_decision


def action(name, target="", duration=1):
    return Decision(action=name, target_id=target, duration=duration, say="Хорошо")


@pytest.mark.parametrize("name,target", [
    ("follow", "leader"), ("attack", "enemy"), ("pickup", "drop"),
    ("eat", "inventory:0"), ("equip", "inventory:1"), ("wait", ""),
])
def test_supported_actions(snapshot, name, target):
    assert validate_decision(action(name, target), Snapshot(**snapshot)).action == name


@pytest.mark.parametrize("name,target", [
    ("attack", "leader"), ("pickup", "inventory:0"), ("follow", "enemy"),
    ("eat", "drop"), ("equip", "inventory:0"), ("attack", "made-up"),
])
def test_invented_or_wrong_target_rejected(snapshot, name, target):
    with pytest.raises(ValueError):
        validate_decision(action(name, target), Snapshot(**snapshot))


@pytest.mark.parametrize("duration", [0, 3.1, -1, float("nan"), float("inf")])
def test_unbounded_actions_rejected(duration):
    with pytest.raises(ValidationError):
        action("wait", duration=duration)


def test_arbitrary_code_not_an_action():
    with pytest.raises(ValidationError):
        Decision(action="execute", target_id="", duration=1, say="", code="spawn SwordCheat")


def test_recompute_distance(snapshot):
    snapshot["entities"][1]["position"]["x"] = 300
    with pytest.raises(ValueError):
        validate_decision(action("attack", "enemy"), Snapshot(**snapshot))


def test_duplicate_ids_rejected(snapshot):
    snapshot["entities"][1]["id"] = "leader"
    with pytest.raises(ValidationError):
        Snapshot(**snapshot)


def test_missing_leader_rejected(snapshot):
    snapshot["leader_id"] = "unknown"
    with pytest.raises(ValidationError):
        Snapshot(**snapshot)
