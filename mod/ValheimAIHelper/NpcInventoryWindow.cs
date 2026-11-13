using System;
using System.Linq;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

// A human opens the vanilla chest UI against the NPC's actual inventory. The
// inert adapter has no inventory of its own, RPCs, ZDO item saves or loot hooks.
internal static class NpcInventoryWindow
{
    private static NpcInventoryProxy? current;
    private static bool hooksReady;

    internal static void Initialize(Harmony harmony)
    {
        hooksReady = false;
        foreach (var name in new[] { "m_inventory", "m_nview" })
            if (UiReflection.Field(typeof(Container), name) == null) throw new MissingFieldException(typeof(Container).FullName, name);
        foreach (var name in new[] { "m_firstContainerUpdate", "m_containerHoldTime", "m_containerHoldState" })
            if (UiReflection.Field(typeof(InventoryGui), name) == null) throw new MissingFieldException(typeof(InventoryGui).FullName, name);
        foreach (var patch in new[] { typeof(NpcContainerAwakePatch), typeof(NpcContainerUsePatch),
            typeof(NpcContainerStackPatch), typeof(NpcContainerInteractPatch), typeof(NpcContainerTakePatch),
            typeof(NpcInventorySelectedPatch), typeof(NpcInventoryTakeAllPatch), typeof(NpcInventoryStackAllPatch), typeof(NpcInventoryDropPatch), typeof(NpcInventoryRightClickPatch),
            typeof(NpcInventoryHoverPatch), typeof(NpcInventoryTitlePatch) }) harmony.PatchAll(patch);
        hooksReady = true;
    }

    internal static void Attach(Player npc)
    {
        if (CompanionNpc.Is(npc) && !npc.GetComponent<NpcInventoryInteraction>())
            npc.gameObject.AddComponent<NpcInventoryInteraction>();
    }

    internal static bool IsOpenFor(Player npc) => npc && current && current!.Npc == npc && current.InUse;

    internal static bool TryOpen(Player npc, Action beforeOpen, out string error)
    {
        error = "";
        if (!CanOpen(npc, out error)) return false;
        Close();
        try
        {
            // Stop/cancel the executor before exposing the bag to human input.
            // It stays stopped when the window is closed; no surprise resumption.
            beforeOpen();
            NativeMethods.Call(npc, "ClearActionQueue");
            var gui = InventoryGui.instance;
            if (InventoryGui.IsVisible()) gui.Hide();
            var go = new GameObject("AIHelper_NpcInventory");
            go.transform.SetParent(npc.transform, false);
            var proxy = go.AddComponent<NpcInventoryProxy>();
            proxy.Npc = npc;
            current = proxy;
            // Marker and owner must be assigned BEFORE AddComponent invokes Awake.
            proxy.Container = go.AddComponent<Container>();
            // Unity can log an Awake exception without propagating it through
            // AddComponent. Never expose a partially initialized adapter.
            if (!ReferenceEquals(proxy.Container.GetInventory(), npc.GetInventory()))
                throw new InvalidOperationException("NPC inventory adapter is not connected to the actual bag");
            proxy.Container.enabled = false;
            proxy.InUse = true;
            UiReflection.Field(typeof(InventoryGui), "m_firstContainerUpdate")!.SetValue(gui, true);
            UiReflection.Field(typeof(InventoryGui), "m_containerHoldTime")!.SetValue(gui, 0f);
            UiReflection.Field(typeof(InventoryGui), "m_containerHoldState")!.SetValue(gui, 0);
            // Unlike a chest's RPC response, this opens in the same frame as E.
            // Consume that input so InventoryGui.Update does not close it again.
            ZInput.ResetButtonStatus("Use");
            ZInput.ResetButtonStatus("JoyUse");
            NativeMethods.Call(gui, "Show", proxy.Container, 1);
            return true;
        }
        catch (Exception ex)
        {
            Close();
            Plugin.NpcError(ex);
            error = "Не удалось открыть инвентарь NPC: " + ex.GetType().Name + ". " + ex.Message;
            return false;
        }
    }

    private static bool CanOpen(Player npc, out string error, bool existingSession = false)
    {
        var human = Player.m_localPlayer;
        var view = npc ? npc.GetComponent<ZNetView>() : null;
        bool validNpc = npc && CompanionNpc.Is(npc) && npc != human && !npc.IsDead() && view && view!.IsValid();
        bool validHuman = human && !CompanionNpc.Is(human) && !human.IsDead() && !human.IsTeleporting();
        var result = NpcInventoryAccess.Check(hooksReady, validNpc,
            validNpc && npc.GetComponent<CompanionNpc>() && npc.GetComponent<CompanionNpc>().Ready,
            ZNet.instance && ZNet.instance.IsServer(), validNpc && view!.IsOwner(), validHuman,
            validHuman && human.IsOwner(), InventoryGui.instance,
            npc && human ? Vector3.Distance(human.transform.position, npc.transform.position) : double.PositiveInfinity, existingSession);
        error = result switch
        {
            NpcInventoryAccessResult.Allowed => "",
            NpcInventoryAccessResult.Unavailable => "Обмен вещами недоступен: игровые обработчики инвентаря не загружены. См. журнал.",
            NpcInventoryAccessResult.InvalidNpc => "Нет живого NPC для обмена вещами.",
            NpcInventoryAccessResult.LoadingNpc => "NPC ещё загружается; повторите открытие инвентаря.",
            NpcInventoryAccessResult.HostOnly => "Инвентарь NPC доступен на клиенте хозяина мира.",
            NpcInventoryAccessResult.InvalidHuman => "Для обмена нужен живой персонаж игрока.",
            NpcInventoryAccessResult.UiUnavailable => "Игровой инвентарь ещё не готов.",
            _ => "Подойдите к NPC ближе (до 3,5 м), чтобы обменяться вещами."
        };
        return result == NpcInventoryAccessResult.Allowed;
    }

    internal static void Update()
    {
        if (!current) { current = null; return; }
        if (!CanOpen(current!.Npc, out _, true) || !InventoryGui.IsVisible() ||
            NativeAccess.Read(InventoryGui.instance, "m_currentContainer") as Container != current.Container)
            Close();
    }

    internal static void Close()
    {
        var proxy = current;
        current = null;
        if (!proxy) return;
        proxy!.InUse = false;
        try
        {
            var gui = InventoryGui.instance;
            if (gui && proxy.Container && NativeAccess.Read(gui, "m_currentContainer") as Container == proxy.Container)
                gui.Hide(); // also clears any drag/split references into the bag
            else if (gui && proxy.Npc)
            {
                // Another mod may Show a different chest without closing ours.
                // Remove references into this bag without closing its new UI.
                var bag = proxy.Npc.GetInventory();
                if (ReferenceEquals(NativeAccess.Read(gui, "m_dragInventory"), bag))
                    NativeMethods.Call(gui, "SetupDragItem", null, null, 1);
                if (ReferenceEquals(NativeAccess.Read(gui, "m_splitInventory"), bag))
                {
                    var panel = NativeAccess.Object(NativeAccess.Read(gui, "m_splitPanel"));
                    if (panel) panel!.SetActive(false);
                    UiReflection.Field(typeof(InventoryGui), "m_splitInventory")?.SetValue(gui, null);
                    UiReflection.Field(typeof(InventoryGui), "m_splitItem")?.SetValue(gui, null);
                }
            }
        }
        finally
        {
            try
            {
                if (proxy.Npc && proxy.Npc.GetComponent<CompanionNpc>())
                    proxy.Npc.GetComponent<CompanionNpc>().Save();
            }
            catch (Exception ex) { Plugin.NpcError(ex); }
            finally { UnityEngine.Object.Destroy(proxy.gameObject); }
        }
    }

    internal static NpcInventoryProxy? Proxy(Container container) => container ? container.GetComponent<NpcInventoryProxy>() : null;

    // Revalidate on the UI event itself, too: death/ownership changes may happen
    // after Plugin.Update in the same frame. Ordinary chests remain native.
    internal static bool BeforeMutation(InventoryGui gui)
    {
        var container = NativeAccess.Read(gui, "m_currentContainer") as Container;
        var proxy = container ? Proxy(container!) : null;
        if (!proxy)
        {
            if (!current) return true;
            // Show(newChest) can leave the old grid/drag alive until its next
            // refresh. Clear it and discard this one stale event.
            Close(); return false;
        }
        if (proxy == current && proxy!.InUse && CanOpen(proxy.Npc, out _, true)) return true;
        Close();
        if (gui && NativeAccess.Read(gui, "m_currentContainer") as Container == container) gui.Hide();
        return false;
    }

    internal static void Prepare(ItemDrop.ItemData? item)
    {
        if (item == null || !IsCurrentInventory() || !current!.Npc.GetInventory().ContainsItem(item)) return;
        var npc = current.Npc;
        using var scope = new NpcScope();
        bool equipped = npc.IsItemEquiped(item) || ReferenceEquals(NativeAccess.Read(npc, "m_hiddenLeftItem"), item) ||
            ReferenceEquals(NativeAccess.Read(npc, "m_hiddenRightItem"), item);
        npc.RemoveEquipAction(item);
        npc.UnequipItem(item, false);
        if (equipped) npc.GetComponent<CompanionNpc>().Save();
    }

    internal static void PrepareAll()
    {
        if (!IsCurrentInventory()) return;
        foreach (var item in current!.Npc.GetInventory().GetAllItems().ToArray()) Prepare(item);
    }

    private static bool IsCurrentInventory() => current && current!.Npc && InventoryGui.instance &&
        NativeAccess.Read(InventoryGui.instance, "m_currentContainer") as Container == current.Container;

    internal static void PrepareDrag(InventoryGui gui) => Prepare(NativeAccess.Read(gui, "m_dragItem") as ItemDrop.ItemData);

    internal static void UpdateTitle(InventoryGui gui)
    {
        if (!current || !current!.Npc || NativeAccess.Read(gui, "m_currentContainer") as Container != current.Container) return;
        var label = NativeAccess.Read(gui, "m_containerName");
        if (label != null) UiReflection.Write(label, "text", "Инвентарь: " + current.Npc.GetPlayerName());
    }
}

internal sealed class NpcInventoryProxy : MonoBehaviour
{
    internal Player Npc = null!;
    internal Container Container = null!;
    internal bool InUse;
}

internal sealed class NpcInventoryInteraction : MonoBehaviour, Interactable
{
    public bool Interact(Humanoid user, bool hold, bool alt)
    {
        if (hold || user != Player.m_localPlayer) return false;
        var npc = GetComponent<Player>();
        if (!CompanionNpc.Is(npc)) return false;
        Plugin.OpenNpcInventory(npc);
        return true;
    }
    public bool UseItem(Humanoid user, ItemDrop.ItemData item) => false;
}

[HarmonyPatch(typeof(Container), "Awake")]
internal static class NpcContainerAwakePatch
{
    private static bool Prefix(Container __instance)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        if (!proxy!.Npc || !CompanionNpc.Is(proxy.Npc)) throw new InvalidOperationException("NPC inventory adapter has no owner");
        UiReflection.Field(typeof(Container), "m_inventory")!.SetValue(__instance, proxy.Npc.GetInventory());
        UiReflection.Field(typeof(Container), "m_nview")!.SetValue(__instance, proxy.Npc.GetComponent<ZNetView>());
        __instance.m_name = proxy.Npc.GetPlayerName();
        return false;
    }
}

[HarmonyPatch(typeof(Container), "SetInUse")]
internal static class NpcContainerUsePatch
{
    private static bool Prefix(Container __instance, bool inUse)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        proxy!.InUse = inUse;
        return false;
    }
}

[HarmonyPatch(typeof(Container), "StackAll")]
internal static class NpcContainerStackPatch
{
    private static bool Prefix(Container __instance)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        if (NpcInventoryWindow.IsOpenFor(proxy!.Npc) && Player.m_localPlayer && NpcInventoryWindow.BeforeMutation(InventoryGui.instance))
            NativeMethods.Call(proxy.Npc.GetInventory(), "StackAll", Player.m_localPlayer.GetInventory(), true);
        return false;
    }
}

[HarmonyPatch(typeof(Container), "Interact")]
internal static class NpcContainerInteractPatch
{
    private static bool Prefix(Container __instance, Humanoid character, bool hold, ref bool __result)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        __result = false;
        if (!hold && character == Player.m_localPlayer) { Plugin.OpenNpcInventory(proxy!.Npc); __result = true; }
        return false;
    }
}

[HarmonyPatch(typeof(Container), "TakeAll")]
internal static class NpcContainerTakePatch
{
    private static bool Prefix(Container __instance, Humanoid character, ref bool __result)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        __result = false;
        if (character == Player.m_localPlayer && NpcInventoryWindow.IsOpenFor(proxy!.Npc) && NpcInventoryWindow.BeforeMutation(InventoryGui.instance))
        {
            NpcInventoryWindow.PrepareAll();
            Player.m_localPlayer.GetInventory().MoveAll(proxy.Npc.GetInventory());
            __result = true;
        }
        return false;
    }
}

[HarmonyPatch(typeof(InventoryGui), "OnSelectedItem")]
internal static class NpcInventorySelectedPatch
{
    private static bool Prefix(InventoryGui __instance, ItemDrop.ItemData? item)
    {
        if (!NpcInventoryWindow.BeforeMutation(__instance)) return false;
        NpcInventoryWindow.Prepare(item); NpcInventoryWindow.PrepareDrag(__instance); return true;
    }
}

[HarmonyPatch(typeof(InventoryGui), "OnTakeAll")]
internal static class NpcInventoryTakeAllPatch
{
    private static bool Prefix(InventoryGui __instance)
    { if (!NpcInventoryWindow.BeforeMutation(__instance)) return false; NpcInventoryWindow.PrepareAll(); return true; }
}

[HarmonyPatch(typeof(InventoryGui), "OnStackAll")]
internal static class NpcInventoryStackAllPatch
{ private static bool Prefix(InventoryGui __instance) => NpcInventoryWindow.BeforeMutation(__instance); }

[HarmonyPatch(typeof(InventoryGui), "OnDropOutside")]
internal static class NpcInventoryDropPatch
{
    private static bool Prefix(InventoryGui __instance)
    { if (!NpcInventoryWindow.BeforeMutation(__instance)) return false; NpcInventoryWindow.PrepareDrag(__instance); return true; }
}

[HarmonyPatch(typeof(InventoryGui), "OnRightClickItem")]
internal static class NpcInventoryRightClickPatch
{
    private static bool Prefix(InventoryGui __instance, ItemDrop.ItemData? item)
    { if (!NpcInventoryWindow.BeforeMutation(__instance)) return false; NpcInventoryWindow.Prepare(item); return true; }
}

[HarmonyPatch(typeof(Player), "GetHoverText")]
internal static class NpcInventoryHoverPatch
{
    private static void Postfix(Player __instance, ref string __result)
    {
        if (!CompanionNpc.Is(__instance) || __instance.IsDead()) return;
        __result = Localization.instance.Localize(__instance.GetPlayerName() + "\n[<color=yellow><b>$KEY_Use</b></color>] Инвентарь NPC");
    }
}

[HarmonyPatch(typeof(InventoryGui), "UpdateContainer")]
internal static class NpcInventoryTitlePatch
{ private static void Postfix(InventoryGui __instance) => NpcInventoryWindow.UpdateTitle(__instance); }
