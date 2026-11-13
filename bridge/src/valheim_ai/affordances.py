"""Capabilities follow observed mechanics and tools, not translated verb names."""

def can_gather(entity, inventory):
    tool = entity.required_tool
    if not tool:
        tool = "pickaxe" if entity.category in ("MineRock", "MineRock5") else "axe" if entity.category in ("TreeBase", "TreeLog", "DestructibleTree") else ""
    if not tool: return False
    skill = "Pickaxes" if tool == "pickaxe" else "Axes"
    return any(i.count > 0 and i.equipable and i.skill == skill and i.durability > 0 and i.tool_tier >= entity.tool_tier for i in inventory)
