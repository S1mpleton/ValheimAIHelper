"""A constrained decoder must not be guided into impossible task mixtures."""
from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from valheim_ai.protocol import Snapshot
from valheim_ai.task_compiler import parse_tasks, task_request
from valheim_ai.task_pool import TaskSpec


def world(snapshot):
    snapshot["supported_actions"] = ["wait", "speak", "follow", "pickup", "give", "drop"]
    snapshot["entities"][-1]["item_prefab"] = "Stone"
    snapshot["item_catalog"] = [dict(item_prefab="Stone", name="Камень")]
    return Snapshot.model_validate(snapshot)


def complete(node, value, root):
    """Supply explicit bookkeeping for strict cloud fixtures, not production."""
    if "$ref" in node: return complete(root["$defs"][node["$ref"].split("/")[-1]], value, root)
    if "anyOf" in node:
        options = node["anyOf"]
        if isinstance(value, dict) and "kind" in value:
            options = [branch for branch in options if branch["properties"]["kind"]["const"] == value["kind"]]
            options = [branch for branch in options if branch["properties"].get("selection", {}).get("const", "exact") == value.get("selection", "exact")]
        elif value is None:
            options = [branch for branch in options if branch.get("type") == "null"]
        return complete(options[0], value, root)
    if node.get("type") == "object":
        value = deepcopy(value)
        for key in node.get("required", []):
            if key not in value:
                field = node["properties"][key]
                if "const" in field: value[key] = field["const"]
                elif "enum" in field: value[key] = field["enum"][0]
                elif key == "selection": value[key] = "exact"
                else: value[key] = {"array": [], "string": "", "boolean": False, "null": None, "integer": 1, "number": field.get("minimum", 1)}.get(field.get("type"))
        return {key: complete(node["properties"][key], child, root) if key in node["properties"] else child for key, child in value.items()}
    if node.get("type") == "array": return [complete(node["items"], child, root) for child in value]
    return value


VALID_TASKS = [
    {"kind": "follow", "priority": 0},
    {"kind": "collect", "item_prefabs": ["Stone"], "amount": 7, "priority": 19},
    {"kind": "collect", "item_prefabs": ["Stone"], "selection": "all_matching", "amount": 1, "continuous": True, "priority": 67},
    {"kind": "give", "item_prefabs": ["Stone"], "amount": 5, "priority": 48},
    {"kind": "give", "item_prefabs": ["Stone"], "selection": "all_matching", "priority": 26},
    {"kind": "give", "item_prefabs": [], "selection": "all_inventory", "priority": 17},
    {"kind": "drop", "item_prefabs": ["Stone"], "amount": 2, "priority": 18},
    {"kind": "drop", "item_prefabs": [], "selection": "all_inventory", "priority": 41},
    {"kind": "action", "priority": 33, "action": {"action": "wait", "target_id": "", "duration": 1, "say": ""}},
    {"kind": "goal", "objective": "Найди безопасное место", "priority": 90},
]


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
@pytest.mark.parametrize("task", VALID_TASKS, ids=lambda task: task["kind"] + str(task["priority"]))
def test_every_generated_variant_is_accepted_by_native_validation(snapshot, provider, task):
    observed = world(snapshot)
    request = task_request("текущее поручение", observed, provider=provider, batch_id="test")
    raw = {"tasks": [task]}
    if provider != "ollama": raw = complete(request["format"], raw, request["format"])
    errors = list(Draft202012Validator(request["format"]).iter_errors(raw))
    assert not errors, [(list(error.path), error.message[:500]) for error in errors]
    raw.pop("catalog_queries", None)  # The compiler consumes the lookup envelope.
    batch = parse_tasks(raw, observed, request["candidates"], new_task_ids=request["new_task_ids"], require_priority=True)
    assert batch.tasks[0].priority == task["priority"]
    assert batch.tasks[0].kind == task["kind"]


INVALID_TASKS = [
    {"kind": "collect", "priority": 12, "item_prefabs": []},
    {"kind": "collect", "priority": 12},
    {"kind": "collect", "priority": 12, "item_prefabs": ["GhostItem"]},
    {"kind": "collect", "priority": 12, "item_prefabs": ["Stone"], "selection": "all_inventory"},
    {"kind": "collect", "priority": 12, "item_prefabs": ["Stone"], "selection": "all_matching", "amount": 5},
    {"kind": "follow", "priority": 12, "action": {"action": "wait", "target_id": "", "duration": 1, "say": ""}},
    {"kind": "give", "priority": 12, "item_prefabs": []},
    {"kind": "drop", "priority": 12, "item_prefabs": ["Stone"], "selection": "all_inventory"},
    {"kind": "drop", "priority": 12, "item_prefabs": ["Stone"], "continuous": True},
    {"kind": "goal", "priority": 12, "objective": ""},
    {"kind": "follow"},
    {"kind": "follow", "priority": 91},
]


@pytest.mark.parametrize("task", INVALID_TASKS)
def test_grammar_rejects_incoherent_mixtures_that_native_rejects(snapshot, task):
    observed = world(snapshot)
    request = task_request("текущее поручение", observed)
    raw = {"tasks": [task]}
    assert not Draft202012Validator(request["format"]).is_valid(raw)
    with pytest.raises(ValueError): parse_tasks(raw, observed, request["candidates"], require_priority=True)


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
def test_schema_keeps_typed_tasks_native_edit_ids_and_explicit_priority_edits(snapshot, provider):
    existing = [TaskSpec(id="native_existing", kind="follow", priority=29)]
    schema = task_request("поменяй приоритет", world(snapshot), provider=provider, existing_tasks=existing)["format"]
    validator = Draft202012Validator(schema)
    for action in ("cancel", "pause", "resume", "priority"):
        edit = {"action": action, "task_id": "native_existing"}
        if action == "priority": edit["priority"] = 0
        raw = {"tasks": [], "edits": [edit]}
        if provider != "ollama": raw = complete(schema, raw, schema)
        assert validator.is_valid(raw)
        native = {key: value for key, value in raw.items() if key != "catalog_queries"}
        assert parse_tasks(native, world(snapshot), existing_tasks=existing).edits[0].task_id == "native_existing"
        if action == "priority":
            raw["edits"][0].pop("priority")
            assert not validator.is_valid(raw)
            with pytest.raises(ValueError): parse_tasks(native, world(snapshot), existing_tasks=existing)
    assert set(schema["$defs"]["Action"]["enum"]) == {"wait", "speak"}


def test_empty_catalog_disables_concrete_item_tasks_without_disabling_goals(snapshot):
    snapshot["actor"]["inventory"] = []
    snapshot["entities"] = snapshot["entities"][:1]
    schema = task_request("найди камни", Snapshot.model_validate(snapshot))["format"]
    assert {branch["properties"]["kind"]["const"] for branch in schema["$defs"]["TaskSpec"]["anyOf"]} == {"follow", "give", "drop", "action", "goal"}
    assert schema["properties"]["catalog_queries"]["maxItems"] == 3
    assert schema["properties"]["edits"]["maxItems"] == 0


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
def test_numeric_grammar_uses_legal_duration_and_model_priority_enums(snapshot, provider):
    observed = world(snapshot)
    observed.supported_actions.append("equip")
    existing = [TaskSpec(id="native_existing", kind="follow", priority=29)]
    schema = task_request("Достань факел", observed, provider=provider, existing_tasks=existing)["format"]
    durations = [.25, .5, .75, 1, 1.5, 2, 2.5, 3]
    assert schema["$defs"]["Decision"]["properties"]["duration"]["enum"] == durations
    assert all(branch["properties"]["priority"]["enum"] == list(range(91))
               for branch in schema["$defs"]["TaskSpec"]["anyOf"])
    edit = next(branch for branch in schema["$defs"]["TaskEdit"]["anyOf"] if branch["properties"]["action"]["const"] == "priority")
    assert edit["properties"]["priority"]["enum"] == list(range(91))
    validator = Draft202012Validator(schema)
    raw = {"tasks": [{"kind": "action", "priority": 23,
        "action": {"action": "equip", "target_id": "inventory:1", "duration": 1, "say": "Достаю"}}]}
    if provider != "ollama": raw = complete(schema, raw, schema)
    for duration in durations:
        raw["tasks"][0]["action"]["duration"] = duration
        assert validator.is_valid(raw)
        native = {key: value for key, value in raw.items() if key != "catalog_queries"}
        assert parse_tasks(native, observed, require_priority=True).tasks[0].action.duration == duration
    # Actual local Qwen response had the right action and inventory ID but
    # duration=10 twice: bounds alone were ignored by Ollama's JSON grammar.
    raw["tasks"][0]["action"]["duration"] = 10
    assert not validator.is_valid(raw)
    with pytest.raises(ValueError):
        parse_tasks({key: value for key, value in raw.items() if key != "catalog_queries"}, observed, require_priority=True)


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_every_cloud_object_including_nested_actions_is_strict(snapshot, provider):
    schema = task_request("действуй", world(snapshot), provider=provider)["format"]
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for value in node.values(): visit(value)
        elif isinstance(node, list):
            for child in node: visit(child)
    visit(schema)


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
def test_task_discriminator_precedes_bookkeeping_in_decoder_property_order(snapshot, provider):
    schema = task_request("Следуй и собирай камни", world(snapshot), provider=provider)["format"]
    assert all(next(iter(branch["properties"])) == "kind" for branch in schema["$defs"]["TaskSpec"]["anyOf"])
