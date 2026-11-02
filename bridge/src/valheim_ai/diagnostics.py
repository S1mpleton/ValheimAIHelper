"""Bounded validation diagnostics without raw request input or credential values."""

FIELDS = frozenset({
    "body", "protocol_version", "session_id", "sequence", "actor", "id", "name", "position",
    "forward", "x", "y", "z", "health", "max_health", "stamina", "inventory", "count",
    "equipped", "food", "equipable", "entities", "kind", "distance", "leader_id",
    "previous_result", "supported_actions", "provider", "model", "api_key", "mode", "objective",
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
