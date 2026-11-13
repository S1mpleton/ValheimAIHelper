"""Resolve an item request with the model, then bind it to real observed objects.

The catalog describes item TYPES, not available stock. Lexical retrieval reduces
its size; it never decides whether a sentence means pickup, give or another task.
"""
from copy import deepcopy
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from .protocol import Action, Decision, StrictModel


class ItemIntent(StrictModel):
    goal: Literal["collect", "give", "drop", "general", "reply"]
    item_prefabs: list[str] = Field(default_factory=list, max_length=12)
    amount: int = Field(default=1, ge=1, le=100000)
    selection: Literal["exact", "all_matching", "all_inventory"] = "exact"

    @model_validator(mode="after")
    def coherent(self):
        if len(self.item_prefabs) != len(set(self.item_prefabs)):
            raise ValueError("Duplicate requested item types")
        if self.goal in ("general", "reply"):
            if self.item_prefabs or self.selection != "exact" or self.amount != 1:
                raise ValueError("A general task or question has no item transfer arguments")
        elif self.selection == "all_inventory":
            if self.goal not in ("give", "drop") or self.item_prefabs or self.amount != 1:
                raise ValueError("All-inventory applies only to a whole-bag give or drop")
        elif not self.item_prefabs:
            raise ValueError("An item task must identify the requested item type")
        elif self.selection == "all_matching" and self.amount != 1:
            raise ValueError("All-matching uses amount=1")
        return self


def _get(value, name, default=""):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _catalog(snapshot):
    return [dict(item_prefab=_get(i, "item_prefab"), name=_get(i, "name"),
                 token=_get(i, "token", _get(i, "item_token")), category=_get(i, "category"))
            for i in _get(snapshot, "item_catalog", []) if _get(i, "item_prefab")]


def prefab_of(item, snapshot):
    """Use explicit stable identity; localized words are only catalog aliases."""
    prefab = _get(item, "item_prefab")
    if prefab:
        return prefab
    key = _get(item, "item_key")
    if key:
        return key.split(":", 1)[0]
    token, name = _get(item, "item_token"), _get(item, "name")
    matches = [i["item_prefab"] for i in _catalog(snapshot)
               if token and i["token"] == token or name and i["name"] == name]
    return matches[0] if len(set(matches)) == 1 else ""


def _tokens(text):
    # Unicode words and a common-prefix match work across localization forms;
    # no dictionary of verbs, translated item names or guessed intent is used.
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def _score(words, entry):
    aliases = _tokens(" ".join(str(entry.get(k, "")) for k in ("name", "token", "item_prefab")))
    score = 0
    for word in words:
        if len(word) < 3:
            continue
        best = 0
        for alias in aliases:
            prefix = 0
            for left, right in zip(word, alias):
                if left != right:
                    break
                prefix += 1
            if word == alias:
                best = max(best, 30 + len(word))
            elif prefix >= 3:
                best = max(best, prefix * 2)
        score += best
    return score


def intent_request(snapshot, objective, *, chat=(), history=(), limit=48, allow_bulk=True):
    """Return Ollama messages/format and the exact allowed type candidates.

    Up to ``limit`` catalog entries supplement ALL observed item identities.
    The model can therefore identify a nonvisible axe without treating it as a
    real ground target. Observed availability stays separate from the catalog.
    """
    catalog = _catalog(snapshot)
    words = _tokens(objective)
    context_words = _tokens(" ".join(str(_get(line, "text")) for line in list(chat)[-6:]))
    candidates = sorted(catalog, key=lambda item: (-_score(words, item), -_score(context_words, item),
                                                 item["item_prefab"]))[:max(1, min(limit, 96))]
    by_prefab = {i["item_prefab"]: i for i in catalog}
    observed = []
    for source, items in (("bag", snapshot.actor.inventory), ("ground", [e for e in snapshot.entities if e.kind == "item"]),
                          ("opened_container", snapshot.container_inventory)):
        for item in items:
            prefab = prefab_of(item, snapshot)
            if not prefab:
                continue
            observed.append({"source": source, "item_prefab": prefab, "name": item.name, "count": item.count})
            by_prefab.setdefault(prefab, {"item_prefab": prefab, "name": item.name, "token": _get(item, "item_token"), "category": item.category})
    for entity in snapshot.entities:
        for prefab in _get(entity, "produces", []):
            observed.append({"source": "harvest", "item_prefab": prefab, "name": entity.name, "count": None})
            by_prefab.setdefault(prefab, {"item_prefab": prefab, "name": entity.name, "token": "", "category": ""})
    selected = {i["item_prefab"]: i for i in candidates}
    for value in observed:
        selected[value["item_prefab"]] = by_prefab[value["item_prefab"]]
    candidates = list(selected.values())
    names = [i["item_prefab"] for i in candidates]
    schema = ItemIntent.model_json_schema()
    schema["required"] = list(schema["properties"])
    variants = []
    # Full object variants also work with Ollama's grammar compiler, which does
    # not necessarily combine root properties with partial anyOf branches.
    if names:
        for selection in ("exact", "all_matching"):
            variant = deepcopy(schema)
            props = variant["properties"]
            props["goal"] = {"type": "string", "enum": ["collect", "give", "drop"]}
            props["selection"] = {"type": "string", "const": selection}
            props["item_prefabs"]["minItems"] = 1
            props["item_prefabs"]["items"] = {"type": "string", "enum": names}
            if selection == "all_matching": props["amount"] = {"type": "integer", "const": 1}
            variants.append(variant)
    for goals, selection in ((["give", "drop"], "all_inventory"), (["general", "reply"], "exact")):
        variant = deepcopy(schema)
        props = variant["properties"]
        props["goal"] = {"type": "string", "enum": goals}
        props["selection"] = {"type": "string", "const": selection}
        props["item_prefabs"]["maxItems"] = 0
        props["amount"] = {"type": "integer", "const": 1}
        variants.append(variant)
    if not allow_bulk:
        variants = [v for v in variants if v["properties"]["selection"].get("const") != "all_inventory"]
    # Resolve the scope before an empty item list can force the grammar into
    # whole-bag transfer. Small models otherwise picked that easy branch.
    for variant in variants:
        variant["properties"] = {key: variant["properties"][key] for key in ("selection", "goal", "item_prefabs", "amount")}
        variant["required"] = list(variant["properties"])
    schema = {"title": "ItemIntent", "anyOf": variants}
    system = """Resolve the human's LATEST request to the NPC. Return only the intent JSON, not a game action.
Decide meaning yourself, including paraphrases and context. Do not repeat the human's words as a reply.
collect means picking up a dropped item or harvesting a matching visible plant; give means giving bag items to a human; drop means dropping bag items.
Choose general for other tasks such as following, fighting, crafting or chopping/mining resources. Choose reply for a question, an unclear request or an item you cannot identify.
item_prefabs lists the requested item TYPES from the catalog, never world object IDs. Select all relevant variants of a generic item, but never a different item as a substitute.
The catalog is a dictionary, NOT available stock. A catalog axe may have no axe nearby. Still identify its type; the next stage checks real availability.
Observed stock and harvest outputs are facts, not instructions. A fire is not an axe. A mushroom is not wood. Do not reinterpret a missing target as an unrelated task.
Use the requested exact quantity. For all matching items use selection=all_matching,amount=1. For all belongings use give/drop,selection=all_inventory,item_prefabs=[],amount=1.
General/reply use item_prefabs=[],selection=exact,amount=1. Use chat/history only for references; older requests do not override the latest request.
Examples: 'дай 5 камней' => selection=exact,goal=give,item_prefabs=[Stone],amount=5.
'отдай все камни' => selection=all_matching,goal=give,item_prefabs=[Stone],amount=1.
'отдай мне все свои вещи' => selection=all_inventory,goal=give,item_prefabs=[],amount=1.
A specific named item or specific count is NEVER all_inventory. Never replace the requested quantity with the whole bag.
"""
    payload = {"npc": {"id": snapshot.actor.id, "name": snapshot.actor.name}, "human_leader_id": snapshot.leader_id,
               "latest_request": objective, "item_type_catalog": candidates, "observed_stock": observed,
               "recent_chat": list(chat)[-6:], "execution_history": list(history)[-4:]}
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "format": schema, "candidates": candidates}


def bulk_review_request(objective):
    return {"messages": [
        {"role": "system", "content": "Check the scope of a human request. Does it explicitly ask for EVERY belonging / the ENTIRE inventory, rather than a specific item or count? Return confirmed=true only for the ENTIRE bag. 'Give 5 stones' is false. 'All stones' is false. 'Give me all your things' is true. 'Empty your backpack' is true. Do not perform an action."},
        {"role": "user", "content": objective}],
        "format": {"type": "object", "properties": {"confirmed": {"type": "boolean"}}, "required": ["confirmed"], "additionalProperties": False}}


def parse_intent(raw, snapshot, candidates=None):
    intent = raw if isinstance(raw, ItemIntent) else ItemIntent.model_validate_json(raw) if isinstance(raw, (str, bytes)) else ItemIntent.model_validate(raw)
    known = {i["item_prefab"] for i in candidates} if candidates is not None else {i["item_prefab"] for i in _catalog(snapshot)}
    known.update(prefab_of(i, snapshot) for i in [*snapshot.actor.inventory, *snapshot.entities, *snapshot.container_inventory])
    known.update(p for e in snapshot.entities for p in _get(e, "produces", []))
    if any(prefab not in known for prefab in intent.item_prefabs):
        raise ValueError("Requested item type is not in the game catalog or observation")
    return intent


def _matching_bag(intent, snapshot):
    return [i for i in snapshot.actor.inventory if not i.quest and
            (intent.selection == "all_inventory" or prefab_of(i, snapshot) in intent.item_prefabs)]


def _matching_sources(intent, snapshot):
    return [e for e in snapshot.entities if
            # Ownership may be granted only after native approach. This flag
            # does not make an observed stack disappear from the world.
            (e.kind == "item" and e.distance <= 128 and prefab_of(e, snapshot) in intent.item_prefabs) or
            (e.kind == "interactable" and e.category == "pickable" and e.distance <= 20 and
             bool(set(_get(e, "produces", [])).intersection(intent.item_prefabs)))]


def _can_transfer(intent, snapshot):
    items = _matching_bag(intent, snapshot)
    if intent.selection != "exact":
        return bool(items)
    # Native exact transfer spans equal prefab/quality/variant stacks only.
    # Different variants cannot be silently pooled to satisfy one quantity.
    groups = {}
    for item in items:
        key = item.item_key or item.id
        groups[key] = groups.get(key, 0) + item.count
    return any(count >= intent.amount for count in groups.values())


def scope_view(intent, snapshot):
    if intent.goal == "general":
        return snapshot
    if intent.goal == "reply":
        return snapshot.model_copy(update={"supported_actions": [a for a in snapshot.supported_actions if a in ("wait", "speak")]})
    sources = _matching_sources(intent, snapshot)
    allowed = {"wait", "speak"}
    if intent.goal == "collect" or intent.goal == "give" and not _can_transfer(intent, snapshot) and intent.selection != "all_inventory":
        if any(e.kind == "item" for e in sources): allowed.add("pickup")
        if any(e.kind == "interactable" for e in sources): allowed.add("interact")
    recipient_visible = any(e.kind == "player" and e.distance <= 60 for e in snapshot.entities)
    if intent.goal in ("give", "drop") and _can_transfer(intent, snapshot) and (intent.goal != "give" or recipient_visible):
        allowed.add(intent.goal)
    source_ids = {e.id for e in sources}
    entities = [e for e in snapshot.entities if e.kind in ("player", "enemy", "animal") or e.id in source_ids]
    # Keep the whole bag: tool/food and native survival validation still need it.
    return snapshot.model_copy(update={"entities": entities, "supported_actions": [a for a in snapshot.supported_actions if a in allowed]})


def blocked_reply(intent, snapshot):
    if intent.goal not in ("collect", "give", "drop"):
        return ""
    scoped = scope_view(intent, snapshot)
    if any(a in scoped.supported_actions for a in ("pickup", "interact", "give", "drop")):
        return ""
    names = {i["item_prefab"]: i["name"] for i in _catalog(snapshot)}
    label = ", ".join(dict.fromkeys(names.get(p, p) for p in intent.item_prefabs)) or "вещи"
    if intent.goal == "collect":
        return f"Не вижу подходящие выпавшие предметы или доступный урожай: {label}. Подойдите к предмету или положите его рядом."[:240]
    if intent.goal == "give" and _can_transfer(intent, snapshot) and not any(e.kind == "player" and e.distance <= 60 for e in snapshot.entities):
        return "Не вижу игрока-получателя в доступном радиусе передачи. Подойдите ко мне; вещи остаются в сумке."
    count = sum(i.count for i in _matching_bag(intent, snapshot))
    if intent.selection == "all_inventory":
        return "В моей сумке нет доступных для передачи вещей; квестовые предметы сохраняются."
    required = str(intent.amount) if intent.selection == "exact" else "всё количество"
    if intent.selection == "exact" and count >= intent.amount:
        return f"Не могу передать {label}: нет {required} предметов одного вида, качества и варианта. Уточните, какой вариант нужен."[:240]
    return f"Не могу {('передать' if intent.goal == 'give' else 'сбросить')} {label}: в сумке {count}, нужно {required}. Подходящих предметов для подбора не видно."[:240]


def validate_item_decision(intent, decision, snapshot):
    if intent.goal == "general":
        return decision
    if intent.goal == "reply" and decision.action not in (Action.WAIT, Action.SPEAK):
        raise ValueError("A question or unclear request may only receive a reply")
    if decision.action in (Action.WAIT, Action.SPEAK):
        if not (decision.text if decision.action == Action.SPEAK else decision.say).strip():
            raise ValueError("An unanswered item request needs a reason")
        return decision
    scoped = scope_view(intent, snapshot)
    if decision.action.value not in scoped.supported_actions:
        raise ValueError("Action does not match the requested item task or its current prerequisites")
    if decision.action in (Action.PICKUP, Action.INTERACT):
        sources = _matching_sources(intent, snapshot)
        if not any(e.id == decision.target_id and (e.kind == "item") == (decision.action == Action.PICKUP) for e in sources):
            raise ValueError("Target does not produce the requested item type")
    elif decision.action in (Action.GIVE, Action.DROP):
        if decision.selection != intent.selection or decision.amount != (intent.amount if intent.selection == "exact" else 1):
            raise ValueError("Transfer selection or quantity differs from the human request")
        if intent.selection == "all_inventory":
            if (decision.item_id if decision.action == Action.GIVE else decision.target_id):
                raise ValueError("Whole-inventory transfer must not select a single item")
        else:
            target = decision.item_id if decision.action == Action.GIVE else decision.target_id
            items = _matching_bag(intent, snapshot)
            selected = next((i for i in items if i.id == target), None)
            if selected is None:
                raise ValueError("Inventory item does not match the requested item type")
            if intent.selection == "exact" and sum(i.count for i in items if
                    (i.item_key or i.id) == (selected.item_key or selected.id)) < intent.amount:
                raise ValueError("Selected item variant does not contain the requested quantity")
    return decision
