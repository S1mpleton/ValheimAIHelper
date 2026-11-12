"""Bounded validation diagnostics without raw request input or credential values."""

FIELDS = frozenset({
    "body", "protocol_version", "session_id", "sequence", "actor", "id", "name", "position",
    "forward", "x", "y", "z", "health", "max_health", "stamina", "inventory", "count",
    "equipped", "food", "equipable", "entities", "kind", "distance", "leader_id",
    "previous_result", "supported_actions", "provider", "model", "api_key", "mode", "objective", "request_interval_seconds",
    "category", "damage", "durability", "food_health", "food_stamina", "usable", "can_eat", "weight", "tool_tier", "effect",
    "quality", "max_quality", "armor", "block_power", "ammo_type", "skill", "damage_types", "max_stamina", "food_slots", "weapon", "biome",
    "carry_weight", "max_carry_weight", "free_slots", "comfort", "sheltered", "rested", "wet", "cold", "attached", "sitting", "eitr",
    "station_id", "status_effects", "guardian_power", "guardian_cooldown", "state", "targeting_us", "targeting_leader", "attacking",
    "recipes", "requirements", "craftable", "station", "amount", "build_options", "available", "container_id", "container_inventory",
    "daylight", "weather", "background_planning", "listen_chat", "plan_id", "action_status", "recent_events", "actor_name", "speaker", "text", "explicit_address", "ollama_url",
    "steps", "action", "target_id", "duration", "say", "destination", "run", "item_id", "rotation", "chat_intent", "message_id", "addressed", "reply", "combat_policy", "objective_complete", "pickup_allowed",
    "ground_scan_radius", "ground_items_total", "ground_items_truncated",
})


def validation_diagnostics(errors):
    safe = []
    for error in errors[:8]:
        location = [part if part in FIELDS else "?" if isinstance(part, str) else
                    part if isinstance(part, int) and 0 <= part <= 10000 else "?"
                    for part in error["loc"]]
        kind = error["type"]
        safe.append({"loc": location, "type": kind})
    fields = []
    for error in safe:
        path = ".".join(str(p) for p in error["loc"] if p != "body") or "snapshot"
        fields.append(path + " (" + error["type"] + ")")
    return "Проверьте формат данных: " + "; ".join(fields), safe
