using System;
using System.Collections;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed class NativeInventorySession
{
    public bool Owned { get; private set; }
    private Container? expected;
    public static Container? CurrentContainer => NativeAccess.Read(InventoryGui.instance, "m_currentContainer") as Container;
    public static bool HasAccess(Player player, Container container)
    {
        var view = NativeAccess.Read(container, "m_nview") as ZNetView ?? container.GetComponent<ZNetView>();
        if (!view || !view.IsValid() || !view.IsOwner() || CurrentContainer != container || !InventoryGui.IsVisible() ||
            Vector3.Distance(player.transform.position, container.transform.position) > 3) return false;
        if (NativeAccess.Call(container, "CheckAccess", player.GetPlayerID()) is false) return false;
        return !(NativeAccess.Read(container, "m_checkGuardStone") is true) || PrivateArea.CheckAccess(container.transform.position, 0, false);
    }
    public void Open(Player player, Container? container)
    {
        if (Owned && InventoryGui.IsVisible() && expected == container) return;
        if (!Owned && InventoryGui.IsVisible()) throw new InvalidOperationException("Инвентарь уже открыт игроком");
        Close(); expected = container; Owned = true;
        if (container) container!.Interact(player, false, false);
        else InventoryGui.instance.Show(null, 1);
    }
    public void AdoptStation() { Close(); expected = null; Owned = true; }
    public void Close()
    {
        if (Owned && InventoryGui.instance && InventoryGui.IsVisible() && CurrentContainer == expected)
        {
            var cancel = HarmonyLib.AccessTools.Method(typeof(InventoryGui), "OnCraftCancelPressed");
            cancel?.Invoke(InventoryGui.instance, Array.Empty<object>());
            InventoryGui.instance.Hide();
        }
        Owned = false; expected = null;
    }
    public bool Craft(Player player, Recipe recipe, ItemDrop.ItemData? upgrade)
    {
        Open(player, null);
        var gui = InventoryGui.instance;
        NativeAccess.Call(gui, upgrade == null ? "OnTabCraftPressed" : "OnTabUpgradePressed");
        NativeAccess.Call(gui, "UpdateCraftingPanel", false);
        var list = NativeAccess.Read(gui, "m_availableRecipes") as IList;
        if (list == null) throw new InvalidOperationException("В этой версии игры не найден список рецептов интерфейса");
        for (int i = 0; i < list.Count; i++)
        {
            var pair = list[i];
            if ((NativeAccess.Read(pair, "Recipe", "recipe", "m_recipe") as Recipe) != recipe ||
                (NativeAccess.Read(pair, "ItemData", "item", "m_item") as ItemDrop.ItemData) != upgrade) continue;
            NativeAccess.Call(gui, "SetRecipe", i, false);
            NativeAccess.Call(gui, "UpdateRecipe", player, 0f);
            var button = NativeAccess.Read(gui, "m_craftButton");
            if (NativeAccess.Read(button, "interactable") is not true) return false;
            bool oldScope = NativeInputScope.Active, oldPlace = NativeInputScope.Place;
            try { NativeInputScope.Active = true; NativeInputScope.Place = false; NativeAccess.Call(gui, "OnCraftPressed"); }
            finally { NativeInputScope.Active = oldScope; NativeInputScope.Place = oldPlace; }
            return true;
        }
        return false;
    }
    public static int Transfer(Player player, Container container, ItemDrop.ItemData item, int amount, bool deposit)
    {
        if (!HasAccess(player, container) || item.m_shared.m_questItem) return 0;
        var from = deposit ? player.GetInventory() : container.GetInventory();
        var to = deposit ? container.GetInventory() : player.GetInventory();
        if (!from.ContainsItem(item) || amount < 1 || amount > item.m_stack) return 0;
        player.RemoveEquipAction(item); player.UnequipItem(item);
        int remaining = amount;
        // The game's bounded partial-stack move preserves quality, variant, custom
        // data and inventory callbacks. Both access and ownership were checked above.
        for (int y = 0; y < to.GetHeight() && remaining > 0; y++)
            for (int x = 0; x < to.GetWidth() && remaining > 0; x++)
            {
                var existing = to.GetItemAt(x, y);
                if (existing != null && (existing.m_shared.m_name != item.m_shared.m_name || existing.m_stack >= existing.m_shared.m_maxStackSize)) continue;
                int old = item.m_stack;
                bool moved = to.MoveItemToThis(from, item, remaining, x, y);
                int delta = !from.ContainsItem(item) ? remaining : old - item.m_stack;
                remaining -= Math.Max(0, delta);
                if (moved && !from.ContainsItem(item)) return amount;
            }
        return amount - remaining;
    }
}
