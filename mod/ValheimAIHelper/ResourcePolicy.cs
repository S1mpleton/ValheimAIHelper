using System;
using System.Linq;

namespace ValheimAIHelper;

public static class ResourcePolicy
{
    public static bool IsHarvestResource(string component, bool pickable, bool droppedItem, bool destructibleTree)
    {
        if (pickable || droppedItem) return false;
        return component == "TreeBase" || component == "TreeLog" || component == "MineRock" || component == "MineRock5" || component == "Destructible" && destructibleTree;
    }
    public static bool CanGather(Entity entity, InventoryEntry[] inventory)
    {
        if (entity.required_tool == "") return true; // older snapshots validate in their native motor
        string skill = entity.required_tool == "pickaxe" ? "Pickaxes" : "Axes";
        return inventory.Any(i => i.count > 0 && i.equipable && i.skill == skill && i.durability > 0 && i.tool_tier >= entity.tool_tier);
    }
}
