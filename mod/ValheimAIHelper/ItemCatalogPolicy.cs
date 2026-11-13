using System;
using System.Collections.Generic;
using System.Linq;

namespace ValheimAIHelper;

public static class ItemCatalogPolicy
{
    public const int SnapshotLimit = 4096;
    // Prefab identity stays case-sensitive, as it is in Valheim. Equal display
    // names/localization tokens do not mean two prefabs are the same item type.
    public static ItemCatalogEntry[] Normalize(IEnumerable<ItemCatalogEntry> entries) => entries
        .Where(e => e != null && !string.IsNullOrWhiteSpace(e.item_prefab))
        .GroupBy(e => e.item_prefab, StringComparer.Ordinal).Select(g => g.First())
        .OrderBy(e => e.item_prefab, StringComparer.Ordinal).ToArray();

    public static bool HasArmor(string category) => category is "Helmet" or "Chest" or "Legs" or "Shoulder";
    public static bool HasDamage(string category, bool weapon) => weapon || category is "Ammo" or "AmmoNonEquipable";
    public static bool HasBlock(string category, bool weapon) => category == "Shield" || weapon && category != "Bow";
    public static bool HasSkill(string category, bool weapon) => weapon;
    public static bool HasToolTier(string category, bool weapon) => weapon || category == "Tool";
}
