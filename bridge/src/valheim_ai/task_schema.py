"""Provider grammars for the *same* typed task protocol used by the executor.

Pydantic's JSON schema does not encode TaskSpec's cross-field validators.  A
flat object therefore lets a constrained decoder produce impossible mixtures
(for example collect without items, or follow with a nested attack).  These
variants encode those constraints without choosing goals or priorities.
"""
from copy import deepcopy

from .task_pool import TaskBatch


def _strict_objects(node):
    """The cloud structured-output subset requires every object key."""
    if isinstance(node, dict):
        node.pop("default", None)
        if isinstance(node.get("title"), str): node.pop("title", None)
        if node.get("type") == "object":
            node["required"] = list(node.get("properties", {}))
            node["additionalProperties"] = False
        for child in node.values():
            _strict_objects(child)
    elif isinstance(node, list):
        for child in node:
            _strict_objects(child)


def task_batch_schema(snapshot, candidates, new_task_ids, existing_task_ids, provider="ollama"):
    """Return full TaskBatch grammar, narrowed only by current game facts.

    Local output may omit bookkeeping/default fields.  Every new task and
    every priority edit must supply the model's own priority, including zero.
    Item enums describe known *types*, never availability or requested counts.
    """
    schema = TaskBatch.model_json_schema()
    definitions = schema["$defs"]
    supported = {"wait", *snapshot.supported_actions} - {"follow", "pickup", "give", "drop"}
    definitions["Action"]["enum"] = [value for value in definitions["Action"]["enum"] if value in supported]
    # Ollama's grammar does not enforce numeric minimum/maximum. Use finite
    # legal choices for short action leases so an otherwise correct equip
    # cannot repeatedly fail with duration=10. These remain model choices.
    definitions["Decision"]["properties"]["duration"]["enum"] = [.25, .5, .75, 1, 1.5, 2, 2.5, 3]
    base = deepcopy(definitions["TaskSpec"]["properties"])
    base["id"] = {"type": "string", "enum": list(new_task_ids)}
    base["priority"].pop("default", None)
    base["priority"]["enum"] = list(range(91))
    base["depends_on"]["items"] = {"type": "string", "enum": [*existing_task_ids, *new_task_ids]}
    names = [row["item_prefab"] for row in candidates]
    item_types = {"type": "array", "items": {"type": "string", "enum": names}, "minItems": 1, "maxItems": 12}
    common = ("id", "title", "objective", "priority", "depends_on", "timeout_seconds")
    variants = []

    def variant(kind, fields=(), *, required=(), overrides=None):
        # Local constrained decoders follow property order. Select the task
        # kind before bookkeeping/priority, rather than steering generation
        # into a repeated default branch while those fields are emitted.
        props = {"kind": {"type": "string", "const": kind},
                 **{key: deepcopy(base[key]) for key in (*common, *fields)}}
        # An explicit null remains compatible with compact pre-0.8.2 replies;
        # a nested action can only occur in the atomic task variant.
        props["action"] = {"$ref": "#/$defs/Decision"} if kind == "action" else {"type": "null"}
        props.update(deepcopy(overrides or {}))
        variants.append({"type": "object", "properties": props,
                         "required": ["kind", "priority", *required], "additionalProperties": False})

    variant("follow", ("continuous", "max_distance"), overrides={"continuous": {"type": "boolean", "const": True}})
    if names:
        for selection in ("exact", "all_matching"):
            overrides = {"item_prefabs": item_types, "selection": {"type": "string", "const": selection}}
            if selection == "all_matching": overrides["amount"] = {"type": "integer", "const": 1}
            variant("collect", ("item_prefabs", "amount", "selection", "continuous", "near_leader", "max_distance"),
                    required=("item_prefabs",), overrides=overrides)
    for kind in ("give", "drop"):
        for selection in (("exact", "all_matching", "all_inventory") if names else ("all_inventory",)):
            overrides = {"selection": {"type": "string", "const": selection}, "item_prefabs": item_types}
            if selection == "all_inventory":
                overrides["item_prefabs"] = {"type": "array", "items": {"type": "string"}, "maxItems": 0}
            if selection != "exact": overrides["amount"] = {"type": "integer", "const": 1}
            variant(kind, ("item_prefabs", "amount", "selection", "near_leader", "max_distance"),
                    required=("item_prefabs", "selection") if selection == "all_inventory" else ("item_prefabs",), overrides=overrides)
    variant("action", required=("action",))
    variant("goal", ("continuous", "near_leader", "max_distance"), required=("objective",),
            overrides={"objective": {**deepcopy(base["objective"]), "minLength": 1}})
    definitions["TaskSpec"] = {"anyOf": variants}

    edit_base = definitions["TaskEdit"]["properties"]
    task_id = {"type": "string", "enum": list(existing_task_ids)} if existing_task_ids else deepcopy(edit_base["task_id"])
    edit_variants = []
    for action in ("cancel", "pause", "resume", "priority"):
        props = {"action": {"type": "string", "const": action}, "task_id": task_id}
        if action == "priority":
            props["priority"] = {key: value for key, value in edit_base["priority"].items() if key != "default"}
            props["priority"]["enum"] = list(range(91))
        edit_variants.append({"type": "object", "properties": deepcopy(props),
                              "required": list(props), "additionalProperties": False})
    definitions["TaskEdit"] = {"anyOf": edit_variants}
    if not existing_task_ids: schema["properties"]["edits"]["maxItems"] = 0
    schema["properties"]["catalog_queries"] = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 120}, "maxItems": 3}
    if provider != "ollama": _strict_objects(schema)
    return schema
