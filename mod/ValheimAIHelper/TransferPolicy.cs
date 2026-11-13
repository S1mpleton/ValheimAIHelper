using System;
using System.Linq;

namespace ValheimAIHelper;

public static class TransferPolicy
{
    public static bool ValidSelection(string? value) => value == "" || value == null || value == "exact" || value == "all_matching" || value == "all_inventory";
    public static InventoryEntry[] Candidates(InventoryEntry[] bag, string? id, string? selection)
    {
        if (!ValidSelection(selection)) return Array.Empty<InventoryEntry>();
        if (selection == "all_inventory") return bag.Where(i => !i.quest && i.count > 0).ToArray();
        var chosen = bag.FirstOrDefault(i => i.id == id && !i.quest);
        if (chosen == null) return Array.Empty<InventoryEntry>();
        // Empty keys from older bridges/tests never merge unrelated items.
        return bag.Where(i => !i.quest && i.count > 0 && (i.id == id || chosen.item_key.Length > 0 && i.item_key == chosen.item_key)).ToArray();
    }
    public static bool Available(InventoryEntry[] bag, string? id, string? selection, int amount)
    {
        if (!ValidSelection(selection) || amount < 1 || amount > 100000) return false;
        var candidates = Candidates(bag, id, selection);
        return candidates.Length > 0 && (selection == "all_inventory" || selection == "all_matching" || candidates.Sum(i => (long)i.count) >= amount);
    }
}
