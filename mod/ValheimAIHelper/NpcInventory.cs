using System;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

internal static class NpcInventory
{
    internal static bool SelectStation(Player player, CraftingStation station)
    {
        if (!station.InUseDistance(player) || !PrivateArea.CheckAccess(station.transform.position, 0, false) || !station.CheckUsable(player, false)) return false;
        player.SetCraftingStation(station); return true;
    }
    internal static void FindStation(Player player, Recipe recipe)
    {
        var required = recipe.m_craftingStation;
        if (!required) { player.SetCraftingStation(null); return; }
        var station = UnityEngine.Object.FindObjectsOfType<CraftingStation>().Where(s => s && s.m_name == required!.m_name && s.InUseDistance(player))
            .OrderBy(s => Vector3.SqrMagnitude(s.transform.position - player.transform.position)).FirstOrDefault();
        if (station) SelectStation(player, station!);
    }
    internal static bool Craft(Player p, Recipe recipe, ItemDrop.ItemData? upgrade)
    {
        FindStation(p, recipe);
        var bag = p.GetInventory();
        int quality = upgrade == null ? 1 : upgrade.m_quality + 1;
        if (quality > recipe.m_item.m_itemData.m_shared.m_maxQuality || !p.HaveRequirements(recipe, false, quality) ||
            (upgrade != null && (!bag.ContainsItem(upgrade) || upgrade.m_shared.m_name != recipe.m_item.m_itemData.m_shared.m_name))) return false;
        if (NativeAccess.Read(recipe, "m_requireOnlyOneIngredient") is true) return false;
        if (recipe.m_item.m_itemData.m_shared.m_dlc.Length > 0 && !DLCMan.instance.IsDLCInstalled(recipe.m_item.m_itemData.m_shared.m_dlc)) return false;
        if (recipe.m_craftingStation && (!p.GetCurrentCraftingStation() || !p.GetCurrentCraftingStation().CheckUsable(p, false))) return false;
        if (upgrade == null && !bag.CanAddItem(recipe.m_item.gameObject, recipe.m_amount)) return false;
        // Inventory.AddItem/ConsumeResources invoke the same native inventory
        // callbacks as crafting. No creative mode or human profile is used.
        if (upgrade == null)
        {
            var made = bag.AddItem(recipe.m_item.gameObject.name, recipe.m_amount, quality, 0, p.GetPlayerID(), p.GetPlayerName());
            if (made == null) return false;
        }
        else { upgrade.m_quality = quality; upgrade.m_durability = upgrade.GetMaxDurability(); }
        NativeMethods.Call(p, "ConsumeResources", recipe.m_resources, quality, -1, 1);
        NativeMethods.Call(bag, "Changed");
        p.GetComponent<CompanionNpc>()?.Save();
        return true;
    }
    internal static int Repair(Player p, CraftingStation station)
    {
        if (!SelectStation(p, station)) return -1;
        int count = 0;
        foreach (var item in p.GetInventory().GetAllItems())
        {
            if (!item.m_shared.m_canBeReparied || item.m_durability >= item.GetMaxDurability()) continue;
            var recipe = ObjectDB.instance.GetRecipe(item);
            if (!recipe || Math.Min(station.GetLevel(), 4) < recipe!.m_minStationLevel) continue;
            if ((recipe.m_repairStation && recipe.m_repairStation.m_name == station.m_name) ||
                (recipe.m_craftingStation && recipe.m_craftingStation.m_name == station.m_name))
            { item.m_durability = item.GetMaxDurability(); count++; }
        }
        if (count > 0) { NativeMethods.Call(p.GetInventory(), "Changed"); p.GetComponent<CompanionNpc>()?.Save(); }
        return count;
    }
}
