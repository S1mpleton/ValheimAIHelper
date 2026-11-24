"""Atomic task arguments are constrained by the same observed affordances.

The task compiler chooses goals and priorities separately. This grammar only
describes actions and concrete IDs that the current game observation permits.
"""
from copy import deepcopy
import math

from .local_schema import action_schema
from .protocol import Action


ACTION_DURATIONS = [.25, .5, .75, 1, 1.5, 2, 2.5, 3]
TYPED_ACTIONS = {"follow", "pickup", "give", "drop"}
EMOTES = ["wave", "cheer", "point", "sit", "bow", "challenge", "cry", "nonono", "thumbsup"]
ENTITY_ACTIONS = {"attack", "secondary_attack", "defend", "flee", "dodge", "hunt", "gather", "interact",
                  "use_on", "open", "stack", "take", "deposit", "repair", "repair_building", "sleep"}


def task_action_schema(snapshot):
    """Return a standalone Decision schema, including its referenced $defs.

    Integrators can merge ``$defs`` into TaskBatch and store the remaining
    ``anyOf`` under ``$defs.Decision``. Apply cloud strict-object conversion
    afterwards, just as for the rest of TaskBatch.
    """
    known = {action.value for action in Action} - TYPED_ACTIONS
    view = snapshot.model_copy(update={"supported_actions": [
        action for action in snapshot.supported_actions if action in known
    ]})
    schema = action_schema(view, planning=False)
    if "interact" in view.supported_actions and not any(
            branch["properties"]["action"]["const"] == "interact" for branch in schema["anyOf"]):
        # The legacy builder omits this branch when every interactable is
        # farther than20m. Keep its ordinary no-extra-arguments shape so the
        # target pass below can retain valid distant collectible IDs.
        interaction = deepcopy(schema["anyOf"][0])  # wait is always available
        interaction["properties"]["action"] = {"type": "string", "const": "interact"}
        schema["anyOf"].append(interaction)
    entities = {entity.id: entity for entity in snapshot.entities}
    actor = snapshot.actor.position
    variants = []
    for variant in schema["anyOf"]:
        props = variant["properties"]
        action = props["action"]["const"]
        props["duration"] = {"type": "number", "enum": list(ACTION_DURATIONS)}
        if action not in ("take", "deposit", "use_on", "craft"):
            # Compact task replies can omit an unused amount; Decision's
            # default is exactly1. Cloud strict conversion will require it.
            variant["required"] = [name for name in variant["required"] if name != "amount"]
        if action == "interact":
            # The older local one-step schema used a uniform 20m radius. The
            # native executor also approaches collectible Pickables up to128m.
            props["target_id"]["enum"] = [entity.id for entity in snapshot.entities
                if entity.kind == "interactable" and entity.distance <= (
                    128 if entity.category == "pickable" and entity.produces else 20)]
        permitted = []
        for target_id in props["target_id"]["enum"]:
            entity = entities.get(target_id)
            if action in ENTITY_ACTIONS and entity is None:
                continue
            if entity is not None:
                if action in ("take", "deposit") and entity.category != "container":
                    continue
                point = entity.position
                radius = 128 if action == "interact" and entity.category == "pickable" and entity.produces else 20
                if math.dist((actor.x, actor.y, actor.z), (point.x, point.y, point.z)) > radius:
                    continue
            permitted.append(target_id)
        if not permitted:
            continue
        props["target_id"]["enum"] = permitted
        if action == "emote":
            props["text"] = {"type": "string", "enum": list(EMOTES)}
        elif action == "speak":
            props["text"]["minLength"] = 1
        elif action == "craft":
            # The executor accepts at most ten native craft operations. As for
            # duration, an enum works with Ollama where numeric bounds do not.
            props["amount"] = {"type": "integer", "enum": list(range(1, 11))}
        variants.append(variant)
    schema["anyOf"] = variants
    schema["$defs"]["Action"]["enum"] = [variant["properties"]["action"]["const"] for variant in variants]
    return deepcopy(schema)
