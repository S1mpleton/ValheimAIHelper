import copy

import pytest


@pytest.fixture
def snapshot():
    return copy.deepcopy({
        "protocol_version": 1,
        "session_id": "session-1",
        "sequence": 1,
        "actor": {
            "id": "player:1234", "name": "Эйрик", "position": {"x": 0, "y": 0, "z": 0},
            "health": 25, "max_health": 25, "stamina": 50,
            "inventory": [
                {"id": "inventory:0", "name": "Малина", "count": 2, "equipped": False, "food": True, "equipable": False},
                {"id": "inventory:1", "name": "Дубина", "count": 1, "equipped": False, "food": False, "equipable": True},
            ],
        },
        "entities": [
            {"id": "leader", "name": "Игрок", "kind": "player", "position": {"x": 5, "y": 0, "z": 0}, "distance": 5},
            {"id": "enemy", "name": "Грейлинг", "kind": "enemy", "position": {"x": 4, "y": 0, "z": 0}, "distance": 4},
            {"id": "drop", "name": "Камень", "kind": "item", "position": {"x": 1, "y": 0, "z": 0}, "distance": 1},
        ],
        "leader_id": "leader", "previous_result": "Ожидание",
    })
