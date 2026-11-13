using System;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

// NPC transactions never use InventoryGui: that singleton belongs to the human.
internal sealed class NativeInventorySession
{
    public bool Owned { get; private set; }
    private Container? expected;
    private static Container? opened;
    private static Player? owner;
    public static Container? CurrentContainer => opened;
    public static bool HasAccess(Player player, Container container)
    {
        var view = NativeAccess.Read(container, "m_nview") as ZNetView ?? container.GetComponent<ZNetView>();
        if (!view || !view.IsValid() || !view.IsOwner() || CurrentContainer != container || owner != player ||
            Vector3.Distance(player.transform.position, container.transform.position) > 3) return false;
        // A human can take priority even when host and NPC share a network peer.
        if (InventoryGui.instance && InventoryGui.IsVisible() && NativeAccess.Read(InventoryGui.instance, "m_currentContainer") as Container == container) return false;
        if (NativeAccess.Call(container, "CheckAccess", player.GetPlayerID()) is false) return false;
        return !(NativeAccess.Read(container, "m_checkGuardStone") is true) || PrivateArea.CheckAccess(container.transform.position, 0, false);
    }
    public void Open(Player player, Container? container)
    {
        if (Owned && expected == container && HasAccess(player, container!)) return;
        Close();
        if (!container) return;
        var view = container!.GetComponent<ZNetView>();
        if (!view || !view.IsValid() || !view.IsOwner() || (bool)(NativeMethods.Call(container, "IsInUse") ?? true)) return;
        expected = opened = container; owner = player; Owned = true;
        if (!HasAccess(player, container)) { Close(); return; }
        NativeMethods.Call(container, "SetInUse", true);
    }
    public void Close()
    {
        if (Owned && expected)
        {
            var view = expected!.GetComponent<ZNetView>();
            if (view && view.IsValid() && view.IsOwner()) NativeMethods.Call(expected, "SetInUse", false);
        }
        if (opened == expected) { opened = null; owner = null; }
        Owned = false; expected = null;
    }
    public static int Transfer(Player player, Container container, ItemDrop.ItemData item, int amount, bool deposit)
    {
        if (!HasAccess(player, container) || item.m_shared.m_questItem) return 0;
        var from = deposit ? player.GetInventory() : container.GetInventory();
        var to = deposit ? container.GetInventory() : player.GetInventory();
        if (!from.ContainsItem(item) || amount < 1 || amount > item.m_stack ||
            (!deposit && to.GetTotalWeight() + item.GetWeight() / Math.Max(1, item.m_stack) * amount > player.GetMaxCarryWeight())) return 0;
        if (deposit) { player.RemoveEquipAction(item); player.UnequipItem(item); }
        int remaining = amount;
        for (int y = 0; y < to.GetHeight() && remaining > 0; y++)
            for (int x = 0; x < to.GetWidth() && remaining > 0; x++)
            {
                var existing = to.GetItemAt(x, y);
                if (existing != null && (existing.m_shared.m_name != item.m_shared.m_name || existing.m_stack >= existing.m_shared.m_maxStackSize)) continue;
                int old = item.m_stack;
                to.MoveItemToThis(from, item, remaining, x, y);
                int delta = !from.ContainsItem(item) ? remaining : old - item.m_stack;
                remaining -= Math.Max(0, delta);
                if (!from.ContainsItem(item)) return amount;
            }
        return amount - remaining;
    }
    public static int Stack(Player player, Container container)
    {
        if (!HasAccess(player, container)) return 0;
        int total = 0;
        foreach (var item in player.GetInventory().GetAllItems().ToArray())
            if (container.GetInventory().GetAllItems().Any(i => i.m_shared.m_name == item.m_shared.m_name && i.m_quality == item.m_quality))
                total += Transfer(player, container, item, item.m_stack, true);
        return total;
    }
}
