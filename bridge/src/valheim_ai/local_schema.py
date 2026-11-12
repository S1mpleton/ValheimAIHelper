"""Constrain small local models to arguments that actually exist in this view."""
from copy import deepcopy

from .protocol import Decision, Plan


def action_schema(snapshot, planning, chat=(), history=()):
    schema = (Plan if planning else Decision).model_json_schema()
    base = schema["$defs"]["Decision"] if planning else schema
    properties = base["properties"]
    inventory = snapshot.actor.inventory
    entities = snapshot.entities
    kinds = lambda kind, radius=20: [e.id for e in entities if e.kind == kind and e.distance <= radius]
    categories = lambda category: [e.id for e in entities if e.category == category and e.distance <= 20]
    bags = [i.id for i in inventory]
    targets = {
        "follow": [snapshot.leader_id] if snapshot.leader_id else [],
        "give": kinds("player", 60) if bags else [],
        "pickup": [e.id for e in entities if e.kind == "item" and e.distance <= 128],
        "drop": bags, "unequip": [i.id for i in inventory if i.equipable],
        "equip": [i.id for i in inventory if i.equipable],
        "eat": [i.id for i in inventory if i.food and i.can_eat], "use": [i.id for i in inventory if i.usable],
        "gather": kinds("resource"), "hunt": kinds("animal"), "interact": kinds("interactable"),
        "use_on": kinds("interactable") if bags else [],
        "open": categories("container"), "stack": categories("container"),
        "take": [snapshot.container_id] if snapshot.container_id and snapshot.container_inventory else [],
        "deposit": [snapshot.container_id] if snapshot.container_id and bags else [],
        "repair": categories("station"), "repair_building": categories("structure"), "sleep": categories("bed"),
        "craft": [r.id for r in snapshot.recipes if r.craftable],
        "upgrade": [r.id for r in snapshot.recipes] if bags else [],
        "build": [b.id for b in snapshot.build_options if b.available],
    }
    for action in ("attack", "secondary_attack", "defend", "flee", "dodge"): targets[action] = kinds("enemy")
    variants = []
    for action in dict.fromkeys(["wait", *snapshot.supported_actions]):
        ids = targets.get(action, [""])
        if not ids: continue
        names = ["action", "target_id", "duration", "say", "amount"]
        if action in ("give", "take", "deposit", "use_on", "upgrade"): names.append("item_id")
        if action in ("speak", "emote"): names.append("text")
        args = {name: deepcopy(properties[name]) for name in names}
        args["action"] = {"const": action, "type": "string"}
        args["duration"] = {"type": "number", "const": 1}
        args["target_id"]["enum"] = ids
        if "item_id" in args:
            args["item_id"]["enum"] = [i.id for i in snapshot.container_inventory] if action == "take" else bags
        if action not in ("give", "drop", "take", "deposit", "use_on", "craft"):
            args["amount"] = {"type": "integer", "const": 1}
        if action in ("move", "jump", "look", "build"):
            args["destination"] = deepcopy(properties["destination"])
            if action in ("look", "build"):
                args["destination"] = {"$ref": "#/$defs/Vec3"}; names.append("destination")
        if action in ("move", "jump", "follow", "pickup", "give"): args["run"] = {"type": "boolean"}
        if action == "build": args["rotation"] = deepcopy(properties["rotation"])
        variants.append({"type": "object", "properties": args, "required": names, "additionalProperties": False})
    if planning:
        schema["$defs"]["Decision"] = {"anyOf": variants}
        schema["required"] = ["steps", "chat_intent", "combat_policy", "objective_complete"]
        schema["properties"]["steps"]["maxItems"] = 3
        pending = [m["id"] for m in chat if m.get("pending")]
        if pending:
            schema["$defs"]["ChatIntent"]["properties"]["message_id"]["enum"] = pending
        else:
            schema["properties"]["chat_intent"] = {"type": "null"}
        if not history: schema["properties"]["objective_complete"] = {"type": "boolean", "const": False}
    else:
        schema = {"anyOf": variants, "$defs": schema.get("$defs", {})}
    schema.get("$defs", {}).get("Action", {})["enum"] = list(dict.fromkeys(["wait", *snapshot.supported_actions]))
    return schema
